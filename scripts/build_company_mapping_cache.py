from __future__ import annotations

import json

from radar_company_mapping import cache_path, ensure_company_mapping_cache
from radar_industry_registry import load_industry_registry


def main() -> None:
    registry = load_industry_registry()
    payload = ensure_company_mapping_cache(
        registry,
        build_if_missing=True,
        refresh_stale=True,
    )
    print(
        json.dumps(
            {
                "cache_path": str(cache_path()),
                "generated_at": payload.get("generated_at", ""),
                "entry_count": payload.get("entry_count", 0),
                "status": payload.get("status", "built"),
                "error_count": payload.get("error_count", 0),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
