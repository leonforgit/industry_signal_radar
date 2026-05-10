from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from radar_config import DEFAULT_CONFIG_PATH, load_config_section
from radar_upstream_bridge import load_deployment_config, run_remote_python


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_QUANT_CONFIG = ROOT.parent / "qlib_paper_trading" / "config" / "runtime_defaults.json"
DEFAULT_LOCAL_QUERY_SCRIPT = ROOT.parent / "qlib_paper_trading" / "scripts" / "query_quant_research.py"


@dataclass(frozen=True)
class SignalQueryResult:
    status: str
    note: str
    payload: dict[str, Any]


def _signal_config(config_path: Path | None) -> dict[str, Any]:
    cfg = load_config_section(config_path, "signal_access")
    if cfg:
        return cfg
    return {
        "enabled": True,
        "quant_runtime_defaults_paths": [
            str(DEFAULT_LOCAL_QUANT_CONFIG),
            "/opt/quant-runtime/config/runtime_defaults.json",
        ],
        "query_script_paths": [
            str(DEFAULT_LOCAL_QUERY_SCRIPT),
            "/opt/quant-runtime/scripts/query_quant_research.py",
        ],
        "remote_python_bin": "/opt/quant-runtime/.venv/bin/python",
        "timeout_seconds": 120,
    }


def _existing_path(raw_paths: list[Any]) -> Path:
    for raw in raw_paths:
        candidate = Path(str(raw or "")).expanduser()
        if candidate.exists():
            return candidate
    return Path("")


def _preferred_remote_path(raw_paths: list[Any]) -> str:
    for raw in raw_paths:
        candidate = str(raw or "").strip()
        if candidate.startswith("/root/"):
            return candidate
    return ""


def _config_prefers_remote(quant_config_path: Path) -> bool:
    try:
        payload = json.loads(quant_config_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    root_candidates: list[str] = []
    for section in ("alpha158_subsystem", "sentiment_subsystem", "timesfm_subsystem", "research_bundle", "research_registry"):
        value = payload.get(section)
        if not isinstance(value, dict):
            continue
        output_root = str(value.get("output_root") or "").strip()
        if output_root:
            root_candidates.append(output_root)
    return bool(root_candidates) and all(root.startswith("/root/") for root in root_candidates)


def _normalize_symbol(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    if not text:
        return ""
    if "." in text:
        left, right = text.split(".", 1)
        left = left.strip()
        right = right.strip()
        if right in {"SH", "SZ", "HK"}:
            return f"{right}{left}"
    if text.startswith(("SH", "SZ", "HK")):
        return text.replace(".", "")
    return text.replace(".", "")


def _run_query(
    *,
    script_path: Path,
    quant_config_path: Path,
    mode: str,
    timeout_seconds: int,
    symbol: str = "",
    refresh_if_missing: bool = False,
    refresh_upstream: bool = False,
) -> SignalQueryResult:
    if not script_path.exists():
        return SignalQueryResult(status="missing", note=f"query_script_missing:{script_path}", payload={})
    if not quant_config_path.exists():
        return SignalQueryResult(status="missing", note=f"quant_config_missing:{quant_config_path}", payload={})
    command = [sys.executable, str(script_path), "--config", str(quant_config_path), "--mode", mode]
    if mode == "instrument":
        normalized_symbol = _normalize_symbol(symbol)
        if not normalized_symbol:
            return SignalQueryResult(status="missing", note="missing_symbol", payload={})
        command.extend(["--symbol", normalized_symbol])
    if refresh_if_missing:
        command.append("--refresh-if-missing")
    if refresh_upstream:
        command.append("--refresh-upstream")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=max(5, timeout_seconds))
    except subprocess.TimeoutExpired:
        return SignalQueryResult(status="timeout", note=f"query_timeout:{mode}", payload={})
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit_code={result.returncode}"
        return SignalQueryResult(status="error", note=f"query_failed:{detail[:240]}", payload={})
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return SignalQueryResult(status="error", note="query_returned_non_json", payload={})
    if not isinstance(payload, dict):
        return SignalQueryResult(status="error", note="query_returned_non_object", payload={})
    return SignalQueryResult(status="pass", note="", payload=payload)


class RadarSignalAccessFacade:
    def __init__(
        self,
        *,
        query_script_path: Path,
        quant_config_path: Path,
        remote_query_script: str,
        remote_quant_config: str,
        remote_python_bin: str,
        remote_host: str,
        ssh_options: str,
        timeout_seconds: int,
    ) -> None:
        self.query_script_path = query_script_path
        self.quant_config_path = quant_config_path
        self.remote_query_script = remote_query_script
        self.remote_quant_config = remote_quant_config
        self.remote_python_bin = remote_python_bin
        self.remote_host = remote_host
        self.ssh_options = ssh_options
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_config(cls, config_path: Path | None = None) -> "RadarSignalAccessFacade":
        cfg = _signal_config(config_path or DEFAULT_CONFIG_PATH)
        query_script_candidates = list(cfg.get("query_script_paths") or [])
        quant_config_candidates = list(cfg.get("quant_runtime_defaults_paths") or [])
        query_script_path = _existing_path(query_script_candidates)
        quant_config_path = _existing_path(quant_config_candidates)
        remote_query_script = _preferred_remote_path(query_script_candidates)
        remote_quant_config = _preferred_remote_path(quant_config_candidates)
        timeout_seconds = max(5, int(cfg.get("timeout_seconds") or 120))
        deployment = load_deployment_config(config_path or DEFAULT_CONFIG_PATH)
        return cls(
            query_script_path=query_script_path,
            quant_config_path=quant_config_path,
            remote_query_script=remote_query_script,
            remote_quant_config=remote_quant_config,
            remote_python_bin=str(cfg.get("remote_python_bin") or "/opt/quant-runtime/.venv/bin/python"),
            remote_host=str(deployment.get("server_host") or "").strip(),
            ssh_options=str(deployment.get("ssh_options") or "").strip(),
            timeout_seconds=timeout_seconds,
        )

    def enabled(self) -> bool:
        return (
            (self.query_script_path.exists() and self.quant_config_path.exists())
            or bool(self.remote_host and self.remote_query_script and self.remote_quant_config)
        )

    def _can_query_remote(self) -> bool:
        return bool(self.remote_host and self.remote_query_script and self.remote_quant_config)

    def _should_prefer_remote(self) -> bool:
        if self._can_query_remote() and self.quant_config_path.exists() and _config_prefers_remote(self.quant_config_path):
            return True
        return False

    def _run_remote(
        self,
        *,
        mode: str,
        symbol: str = "",
        refresh_if_missing: bool = False,
        refresh_upstream: bool = False,
    ) -> SignalQueryResult:
        if not self._can_query_remote():
            return SignalQueryResult(status="missing", note="remote_signal_access_unavailable", payload={})
        script_args = ["--config", self.remote_quant_config, "--mode", mode]
        if mode == "instrument":
            normalized_symbol = _normalize_symbol(symbol)
            if not normalized_symbol:
                return SignalQueryResult(status="missing", note="missing_symbol", payload={})
            script_args.extend(["--symbol", normalized_symbol])
        if refresh_if_missing:
            script_args.append("--refresh-if-missing")
        if refresh_upstream:
            script_args.append("--refresh-upstream")
        payload, message = run_remote_python(
            host=self.remote_host,
            ssh_options=self.ssh_options,
            python_bin=self.remote_python_bin,
            script_path=self.remote_query_script,
            script_args=script_args,
            timeout_seconds=self.timeout_seconds,
        )
        if payload is None:
            return SignalQueryResult(status="error", note=f"remote_query_failed:{message[:240]}", payload={})
        if not isinstance(payload, dict):
            return SignalQueryResult(status="error", note="remote_query_returned_non_object", payload={})
        return SignalQueryResult(status="pass", note="remote_query", payload=payload)

    def query_market(self, *, refresh_if_missing: bool = False, refresh_upstream: bool = False) -> SignalQueryResult:
        local_result: SignalQueryResult | None = None
        if self.query_script_path.exists() and self.quant_config_path.exists() and not self._should_prefer_remote():
            local_result = _run_query(
                script_path=self.query_script_path,
                quant_config_path=self.quant_config_path,
                mode="market",
                timeout_seconds=self.timeout_seconds,
                refresh_if_missing=refresh_if_missing,
                refresh_upstream=refresh_upstream,
            )
            if local_result.status == "pass" or not self._can_query_remote():
                return local_result
        remote_result = self._run_remote(
            mode="market",
            refresh_if_missing=refresh_if_missing,
            refresh_upstream=refresh_upstream,
        )
        if remote_result.status == "pass":
            return remote_result
        return local_result or remote_result

    def query_instrument(
        self,
        symbol: str,
        *,
        refresh_if_missing: bool = False,
        refresh_upstream: bool = False,
    ) -> SignalQueryResult:
        local_result: SignalQueryResult | None = None
        if self.query_script_path.exists() and self.quant_config_path.exists() and not self._should_prefer_remote():
            local_result = _run_query(
                script_path=self.query_script_path,
                quant_config_path=self.quant_config_path,
                mode="instrument",
                symbol=symbol,
                timeout_seconds=self.timeout_seconds,
                refresh_if_missing=refresh_if_missing,
                refresh_upstream=refresh_upstream,
            )
            if local_result.status == "pass" or not self._can_query_remote():
                return local_result
        remote_result = self._run_remote(
            mode="instrument",
            symbol=symbol,
            refresh_if_missing=refresh_if_missing,
            refresh_upstream=refresh_upstream,
        )
        if remote_result.status == "pass":
            return remote_result
        return local_result or remote_result
