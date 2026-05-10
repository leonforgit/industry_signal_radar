#!/usr/bin/env python3
"""Helpers for discovering a healthy OpenBB HTTP service."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests


DEFAULT_OPENBB_SERVICE_ENV_PATHS = (
    Path("/opt/openbb-runtime/config/openbb_service.env"),
    Path("../openbb/config/openbb_service.env"),
    Path("../openbb/config/openbb_service.env"),
)

DEFAULT_OPENBB_BASE_URL_CANDIDATES = (
    "http://127.0.0.1:6900",
    "http://127.0.0.1:16900",
)


def _load_env_assignments(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _candidate_urls(
    *,
    service_env_paths: list[Path],
    base_url_candidates: list[str],
) -> list[str]:
    urls: list[str] = []
    env_base_url = str(os.environ.get("OPENBB_BASE_URL") or "").strip()
    if env_base_url:
        urls.append(env_base_url.rstrip("/"))
    for env_path in service_env_paths:
        values = _load_env_assignments(env_path)
        host = str(values.get("OPENBB_API_HOST") or "").strip()
        port = str(values.get("OPENBB_API_PORT") or "").strip()
        if host and port:
            urls.append(f"http://{host}:{port}".rstrip("/"))
    for candidate in base_url_candidates:
        text = str(candidate or "").strip()
        if text:
            urls.append(text.rstrip("/"))
    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        deduped.append(url)
        seen.add(url)
    return deduped


def _probe_openbb_url(url: str, timeout_seconds: int) -> tuple[bool, str]:
    session = requests.Session()
    for suffix in ("/openapi.json", "/docs"):
        probe_url = f"{url}{suffix}"
        try:
            response = session.get(probe_url, timeout=max(timeout_seconds, 1))
            if response.ok:
                return True, suffix
        except requests.RequestException as exc:
            note = f"{type(exc).__name__}: {exc}"
        else:
            note = f"HTTP {response.status_code}"
    return False, note


def discover_openbb_service(
    *,
    service_env_paths: list[Path] | None = None,
    base_url_candidates: list[str] | None = None,
    timeout_seconds: int = 3,
) -> dict[str, Any]:
    env_paths = [Path(path) for path in (service_env_paths or list(DEFAULT_OPENBB_SERVICE_ENV_PATHS))]
    candidate_urls = _candidate_urls(
        service_env_paths=env_paths,
        base_url_candidates=[str(item) for item in (base_url_candidates or list(DEFAULT_OPENBB_BASE_URL_CANDIDATES))],
    )
    checked_urls: list[dict[str, str]] = []
    for url in candidate_urls:
        ok, note = _probe_openbb_url(url, timeout_seconds)
        checked_urls.append({"base_url": url, "status": "pass" if ok else "fail", "note": note})
        if ok:
            return {
                "status": "pass",
                "base_url": url,
                "note": f"healthcheck={note}",
                "checked_urls": checked_urls,
                "service_env_paths": [str(path) for path in env_paths],
            }
    return {
        "status": "skip_no_openbb_service",
        "base_url": "",
        "note": "no healthy OpenBB HTTP service discovered",
        "checked_urls": checked_urls,
        "service_env_paths": [str(path) for path in env_paths],
    }


def apply_openbb_service_env(discovery: dict[str, Any]) -> None:
    base_url = str(discovery.get("base_url") or "").strip()
    if discovery.get("status") == "pass" and base_url:
        os.environ["OPENBB_BASE_URL"] = base_url
        os.environ["OPENBB_ENABLED"] = "1"
        return
    os.environ["OPENBB_ENABLED"] = "0"
