# Contributing

Thanks for helping improve Industry Signal Radar.

The project is an event-driven opportunity discovery system. It consumes shared
event feeds, market substrates, and small canonical fixtures, then builds ranked
research handoffs, alerts, and daily reports. Contributions should keep that
boundary clear: repository code and schemas are public; real credentials,
operator runtime configuration, generated state, and private data stay outside
the repository.

## Development Setup

Use a local Python environment and install only the dependencies needed for the
area you are changing. Keep generated outputs out of commits unless a document
or fixture explicitly requires them.

Typical validation commands:

```bash
python3 -m compileall -q scripts
python3 scripts/smoke_test_radar_candidate_pool.py
python3 scripts/smoke_test_shared_news_overlay.py
python3 scripts/smoke_test_radar_news_verification_sidecar.py
python3 scripts/check_radar_contract_cases.py
```

Some integration commands require private upstream feeds or a private runtime
configured outside this repository. When those are unavailable, describe the
skipped check and run the closest synthetic or smoke fixture instead.

## Contribution Rules

- Keep changes focused and reviewable.
- Prefer existing scripts, schemas, contracts, and fixtures over new parallel
  abstractions.
- Add or update tests when changing contracts, ranking behavior, validation, or
  security boundaries.
- Use synthetic sample data for fixtures. Do not add proprietary data dumps,
  paid-source archives, browser traces, cookies, raw-news corpora, or generated
  runtime state.
- Do not commit credentials, SSH material, private hostnames, private ports,
  local absolute paths, or deployment-specific overrides.
- Update `README.md`, `STATUS.md`, and relevant docs when changing project
  structure, contracts, or operator workflow.

## Pull Request Checklist

Before opening a pull request:

- Run the relevant validation commands.
- Confirm that new files are intentionally tracked.
- Confirm that public docs do not expose private infrastructure, personal
  paths, credentials, or source terms that cannot be redistributed.
- Describe any skipped integration checks and why they were skipped.
