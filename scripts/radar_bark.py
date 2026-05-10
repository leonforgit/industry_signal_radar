from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any
from urllib import request

from radar_config import load_config_section


@dataclass
class BarkConfig:
    device_keys: list[str]
    server: str
    group: str
    level: str
    sound: str
    icon_url: str
    is_archive: bool


def parse_device_keys(env_value: str, fallback: str) -> list[str]:
    values = [item.strip() for item in (env_value or "").split(",") if item.strip()]
    if values:
        return values
    if fallback.strip():
        return [fallback.strip()]
    return []


def first_env(candidates: list[str], default: str = "") -> str:
    for name in candidates:
        value = str(os.getenv(name, "")).strip()
        if value:
            return value
    return default


def build_bark_config() -> BarkConfig:
    alerting = load_config_section(None, "alerting")
    bark = alerting.get("bark", {}) if isinstance(alerting.get("bark", {}), dict) else {}
    device_keys_env = str(bark.get("device_keys_env", "BARK_DEVICE_KEYS"))
    device_key_env = str(bark.get("device_key_env", "BARK_DEVICE_KEY"))
    server_env = str(bark.get("server_env", "BARK_SERVER"))
    multi_key_candidates = [device_keys_env, "WATCHLIST_BARK_DEVICE_KEYS", "POLYMARKET_BARK_DEVICE_KEYS"]
    single_key_candidates = [device_key_env, "WATCHLIST_BARK_DEVICE_KEY", "POLYMARKET_BARK_DEVICE_KEY", "BARK_DEVICE_KEY"]
    server_candidates = [server_env, "WATCHLIST_BARK_SERVER", "POLYMARKET_BARK_SERVER", "BARK_SERVER"]
    return BarkConfig(
        device_keys=parse_device_keys(first_env(multi_key_candidates), first_env(single_key_candidates)),
        server=first_env(server_candidates, "https://api.day.app").rstrip("/"),
        group=str(bark.get("group", "industry-signal-radar")).strip(),
        level=str(bark.get("level", "active")).strip(),
        sound=str(bark.get("sound", "")).strip(),
        icon_url=str(bark.get("icon_url", "")).strip(),
        is_archive=bool(bark.get("is_archive", True)),
    )


def build_bark_payload(*, title: str, body: str, bark_config: BarkConfig, url: str = "") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": title,
        "body": body,
        "group": bark_config.group,
        "level": bark_config.level,
        "isArchive": "1" if bark_config.is_archive else "0",
    }
    if bark_config.sound:
        payload["sound"] = bark_config.sound
    if bark_config.icon_url:
        payload["icon"] = bark_config.icon_url
    if url.strip():
        payload["url"] = url.strip()
    return payload


def send_bark_payload(payload: dict[str, Any], bark_config: BarkConfig, timeout_seconds: int = 12) -> dict[str, Any]:
    if not bark_config.device_keys:
        return {
            "sent": False,
            "status": "skipped_no_device_keys",
            "success_count": 0,
            "failure_count": 0,
            "failures": [],
        }

    failures: list[str] = []
    success_count = 0

    for device_key in bark_config.device_keys:
        req = request.Request(
            f"{bark_config.server}/push",
            data=json.dumps({**payload, "device_key": device_key}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
                raw = response.read().decode("utf-8").strip()
            body = json.loads(raw) if raw else {}
            if isinstance(body, dict) and body.get("code") not in (None, 200):
                failures.append(f"{device_key[:8]}...: {body}")
                continue
            success_count += 1
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{device_key[:8]}...: {exc}")

    if success_count > 0:
        return {
            "sent": True,
            "status": "sent" if not failures else "partial_success",
            "success_count": success_count,
            "failure_count": len(failures),
            "failures": failures,
        }

    return {
        "sent": False,
        "status": "failed",
        "success_count": 0,
        "failure_count": len(failures),
        "failures": failures,
    }
