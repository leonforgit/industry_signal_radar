from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "runtime_defaults.json"
DEFAULT_SOURCE_MANIFEST_PATH = ROOT / "config" / "source_manifest.json"


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def load_workspace_config(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    return _load_json_object(target)


def load_source_manifest(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else DEFAULT_SOURCE_MANIFEST_PATH
    return _load_json_object(target)


def load_config_section(path: Path | None, section: str) -> dict[str, Any]:
    payload = load_workspace_config(path)
    value = payload.get(section, {})
    return value if isinstance(value, dict) else {}


def load_manifest_sources(path: Path | None = None) -> list[dict[str, Any]]:
    payload = load_source_manifest(path)
    value = payload.get("sources", [])
    return value if isinstance(value, list) else []
