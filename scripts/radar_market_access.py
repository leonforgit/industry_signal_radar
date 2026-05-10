from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from radar_canonical_retry import call_with_canonical_retries
from radar_config import DEFAULT_CONFIG_PATH, load_config_section
from radar_openbb_service import apply_openbb_service_env, discover_openbb_service
from radar_price_sidecar import resolve_repo_path


@dataclass(frozen=True)
class MarketAccessResult:
    rows: list[dict[str, Any]]
    provider_used: str
    note: str
    source: str


def append_retry_note(note: str, label: str, retry_count: int) -> str:
    if retry_count <= 0:
        return note
    suffix = f"{label}_retries={retry_count}"
    return f"{note}; {suffix}" if note else suffix


def resolve_existing_adapter_path(config_paths: list[Any]) -> Path:
    for raw_path in config_paths:
        path = resolve_repo_path(raw_path, fallback=Path(""))
        if str(path) and path.exists():
            return path
    return Path("")


def load_openbb_adapter(adapter_path: Path, extra_site_packages: list[Path]) -> Any:
    for site_packages in extra_site_packages:
        if site_packages.exists() and str(site_packages) not in sys.path:
            sys.path.insert(0, str(site_packages))
    spec = importlib.util.spec_from_file_location("radar_market_openbb_adapter", adapter_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load OpenBB adapter from {adapter_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bridge_openbb_config(config_path: Path | None) -> dict[str, Any]:
    cfg = load_config_section(config_path, "market_access_facade")
    if cfg:
        return cfg
    price_cfg = load_config_section(config_path, "canonical_price_bridge")
    fundamental_cfg = load_config_section(config_path, "canonical_fundamental_bridge")
    merged = dict(price_cfg)
    for key in ("openbb_service_env_paths", "openbb_base_url_candidates", "openbb_timeout_seconds"):
        if key in fundamental_cfg and key not in merged:
            merged[key] = fundamental_cfg[key]
    return merged


class RadarMarketAccessFacade:
    def __init__(self, *, adapter_module: Any, client: Any, discovery: dict[str, Any], adapter_path: Path) -> None:
        self.adapter_module = adapter_module
        self.client = client
        self.discovery = discovery
        self.adapter_path = adapter_path

    @classmethod
    def from_config(cls, config_path: Path | None = None) -> "RadarMarketAccessFacade":
        cfg = _bridge_openbb_config(config_path or DEFAULT_CONFIG_PATH)
        adapter_path = resolve_existing_adapter_path(
            list(cfg.get("openbb_adapter_paths") or [cfg.get("openbb_adapter_path")])
        )
        extra_site_packages = [
            resolve_repo_path(path, fallback=Path(""))
            for path in (cfg.get("extra_site_packages") or [])
            if str(path).strip()
        ]
        if not adapter_path.exists():
            raise RuntimeError(f"OpenBB adapter path is missing: {adapter_path}")
        service_env_paths = [
            resolve_repo_path(path, fallback=Path(""))
            for path in (cfg.get("openbb_service_env_paths") or [])
            if str(path).strip()
        ]
        base_url_candidates = [str(item) for item in (cfg.get("openbb_base_url_candidates") or []) if str(item).strip()]
        openbb_timeout_seconds = max(1, int(cfg.get("openbb_timeout_seconds") or 3))
        discovery = discover_openbb_service(
            service_env_paths=service_env_paths or None,
            base_url_candidates=base_url_candidates or None,
            timeout_seconds=openbb_timeout_seconds,
        )
        apply_openbb_service_env(discovery)
        adapter_module = load_openbb_adapter(adapter_path, extra_site_packages)
        client = adapter_module.OpenBBClient()
        return cls(adapter_module=adapter_module, client=client, discovery=discovery, adapter_path=adapter_path)

    def canonical_enabled(self) -> bool:
        try:
            return bool(self.client.canonical_db.enabled())
        except Exception:  # pragma: no cover - adapter dependent
            return False

    def resolve_binding(self, symbol: str) -> dict[str, Any] | None:
        if not self.canonical_enabled():
            return None
        binding, _ = call_with_canonical_retries(
            lambda: self.client.canonical_db._resolve_symbol_binding(symbol),  # noqa: SLF001
        )
        return binding

    def get_equity_price_history(
        self,
        *,
        symbol: str,
        target_date: str,
        lookback_days: int = 45,
        force_fetch: bool = False,
    ) -> MarketAccessResult:
        start_date = (datetime.fromisoformat(target_date) - timedelta(days=lookback_days)).date().isoformat()
        existing_rows: list[dict[str, Any]] = []
        canonical_read_note = ""
        if self.canonical_enabled():
            try:
                existing_rows, read_retry_count = call_with_canonical_retries(
                    lambda: self.client.canonical_db.get_equity_price_historical(
                        symbol,
                        start_date=start_date,
                        end_date=target_date,
                    )
                )
                canonical_read_note = append_retry_note(canonical_read_note, "canonical_read", read_retry_count)
            except Exception as exc:  # pragma: no cover - upstream lock / env dependent
                canonical_read_note = f"canonical_read_error={type(exc).__name__}"
                existing_rows = []
        existing_latest = max((str(row.get("date") or "") for row in existing_rows), default="")
        if existing_latest >= target_date and not force_fetch:
            note = "existing_canonical_rows"
            if canonical_read_note:
                note = f"{note}; {canonical_read_note}"
            return MarketAccessResult(rows=existing_rows, provider_used="canonical_db", note=note, source="canonical")

        fetched_rows: list[dict[str, Any]] = []
        provider_used = ""
        if self.adapter_module.is_a_share_symbol(symbol):
            fetched_rows = self.client._get_a_share_price_historical(  # noqa: SLF001
                symbol,
                start_date=start_date,
                end_date=target_date,
            )
        elif self.adapter_module.is_hk_symbol(symbol):
            fetched_rows = self.client._get_hk_price_historical(  # noqa: SLF001
                symbol,
                start_date=start_date,
                end_date=target_date,
            )
        if fetched_rows:
            provider_used = str(fetched_rows[-1].get("provider") or "provider_fetch")
            if self.canonical_enabled():
                try:
                    _, persist_retry_count = call_with_canonical_retries(
                        lambda: self.client.canonical_db.persist_equity_price_historical(
                            symbol,
                            fetched_rows,
                            provider_used=provider_used,
                            source_provider="radar_price_backfill",
                        )
                    )
                    existing_rows, read_retry_count = call_with_canonical_retries(
                        lambda: self.client.canonical_db.get_equity_price_historical(
                            symbol,
                            start_date=start_date,
                            end_date=target_date,
                        )
                    )
                    note = "provider_fetch_persisted"
                    note = append_retry_note(note, "canonical_persist", persist_retry_count)
                    note = append_retry_note(note, "canonical_read", read_retry_count)
                    if canonical_read_note:
                        note = f"{note}; {canonical_read_note}"
                    return MarketAccessResult(
                        rows=existing_rows or fetched_rows,
                        provider_used=provider_used,
                        note=note,
                        source="provider+persistent_cache",
                    )
                except Exception as exc:  # pragma: no cover - upstream lock / env dependent
                    note = f"provider_fetch_local_only; canonical_persist_error={type(exc).__name__}"
                    if canonical_read_note:
                        note = f"{note}; {canonical_read_note}"
                    return MarketAccessResult(rows=fetched_rows, provider_used=provider_used, note=note, source="provider_local_only")
            note = "provider_fetch_local_only"
            if canonical_read_note:
                note = f"{note}; {canonical_read_note}"
            return MarketAccessResult(rows=fetched_rows, provider_used=provider_used, note=note, source="provider_local_only")

        note = "no_fresh_rows"
        if canonical_read_note:
            note = f"{note}; {canonical_read_note}"
        return MarketAccessResult(
            rows=existing_rows,
            provider_used="canonical_db" if existing_rows else "",
            note=note,
            source="canonical_stale" if existing_rows else "missing",
        )

    def get_equity_fundamental_statement(
        self,
        *,
        symbol: str,
        statement: str,
        period: str,
        limit: int = 4,
        force_fetch: bool = False,
    ) -> MarketAccessResult:
        existing_rows: list[dict[str, Any]] = []
        canonical_read_note = ""
        if self.canonical_enabled():
            try:
                existing_rows, read_retry_count = call_with_canonical_retries(
                    lambda: self.client.canonical_db.get_equity_fundamental_statement_results(
                        symbol,
                        statement=statement,
                        period=period,
                        limit=limit,
                    )
                )
                canonical_read_note = append_retry_note(canonical_read_note, "canonical_read", read_retry_count)
            except Exception as exc:  # pragma: no cover - upstream lock / env dependent
                canonical_read_note = f"canonical_read_error={type(exc).__name__}"
                existing_rows = []
        if existing_rows and not force_fetch:
            note = "existing_canonical_rows"
            if canonical_read_note:
                note = f"{note}; {canonical_read_note}"
            return MarketAccessResult(rows=existing_rows, provider_used="canonical_db", note=note, source="canonical")

        path = self.adapter_module.FUNDAMENTAL_STATEMENT_PATHS.get(statement)
        if path is None:
            return MarketAccessResult(rows=[], provider_used="", note="unsupported_statement", source="error")
        provider_name = str(getattr(self.client.config, "default_provider", "") or "")
        provider_used = f"openbb:{provider_name}" if provider_name else "openbb:provider"
        try:
            payload = self.client._request_json(  # noqa: SLF001
                path,
                params={
                    "provider": provider_name,
                    "symbol": symbol,
                    "period": period,
                    "limit": limit,
                },
            )
        except Exception as exc:  # pragma: no cover - upstream provider / service dependent
            return MarketAccessResult(
                rows=[],
                provider_used=provider_used,
                note=f"provider_fetch_error={type(exc).__name__}",
                source="provider_error",
            )
        results = self.adapter_module._extract_results(payload, context=f"{statement} statement")  # noqa: SLF001
        if results and self.canonical_enabled():
            try:
                _, persist_retry_count = call_with_canonical_retries(
                    lambda: self.client.canonical_db.persist_equity_fundamental_statement_results(
                        symbol,
                        statement=statement,
                        period=period,
                        rows=results,
                        provider_used=provider_used,
                        source_provider="radar_fundamental_backfill",
                    )
                )
                canonical_results, read_retry_count = call_with_canonical_retries(
                    lambda: self.client.canonical_db.get_equity_fundamental_statement_results(
                        symbol,
                        statement=statement,
                        period=period,
                        limit=limit,
                    )
                )
                note = "provider_fetch_persisted"
                note = append_retry_note(note, "canonical_persist", persist_retry_count)
                note = append_retry_note(note, "canonical_read", read_retry_count)
                if canonical_read_note:
                    note = f"{note}; {canonical_read_note}"
                return MarketAccessResult(
                    rows=canonical_results or results,
                    provider_used=provider_used,
                    note=note,
                    source="provider+persistent_cache",
                )
            except Exception as exc:  # pragma: no cover - upstream lock / env dependent
                note = f"provider_fetch_local_only; canonical_persist_error={type(exc).__name__}"
                if canonical_read_note:
                    note = f"{note}; {canonical_read_note}"
                return MarketAccessResult(rows=results, provider_used=provider_used, note=note, source="provider_local_only")
        if results:
            note = "provider_fetch_local_only"
            if canonical_read_note:
                note = f"{note}; {canonical_read_note}"
            return MarketAccessResult(rows=results, provider_used=provider_used, note=note, source="provider_local_only")
        note = "no_rows"
        if canonical_read_note:
            note = f"{note}; {canonical_read_note}"
        return MarketAccessResult(rows=existing_rows, provider_used="canonical_db" if existing_rows else provider_used, note=note, source="missing")
