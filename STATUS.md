# STATUS

Updated: 2026-05-10

## Public Status

`Industry Signal Radar` is public-ready open-source research infrastructure for
event-driven opportunity discovery. It is designed as a downstream Radar that
consumes structured event feeds from `News Event Hub`, market/sentiment
sidecars, and local public-safe fixtures, then produces ranked research queues,
opportunity snapshots, battlecards, and quality-gated reports.

The repository is source-only. It does not contain real API keys, SSH material,
SMTP credentials, browser state, production databases, generated reports, raw
news archives, or private deployment overrides.

## What Is In Place

- Open-source governance: `LICENSE`, `SECURITY.md`, `CONTRIBUTING.md`, and
  `docs/public_release_checklist.md`.
- Secret boundary: source-only repository policy, `.gitignore`, public-safe
  smoke tests, and GitHub secret scanning / push protection.
- Public-safe smoke fixtures for candidate pools, shared-news overlays, news
  verification sidecars, harness semantics, and contract cases.
- Core Radar pipeline scripts for candidate generation, opportunity snapshots,
  catalyst inventory, report rendering, freshness checks, and quality gates.
- Public-safe templates for deployment and runtime configuration. Paths under
  `/opt/...` are examples and must be overridden by operators for their own
  environments.

## Current Boundaries

- This is not an automated trading system and does not provide investment
  advice.
- Runtime state, caches, generated outputs, browser auth state, and service
  credentials belong outside Git.
- Optional integrations such as Kimi-compatible model calls, Bark delivery,
  OpenBB/canonical market access, and private event feeds are disabled unless
  explicitly configured by the operator.
- Public examples should use placeholders or synthetic fixtures.

## Validation

Recommended public-safe checks:

```bash
python3 -m compileall -q scripts
python3 scripts/smoke_test_radar_candidate_pool.py
python3 scripts/smoke_test_radar_harness_v2.py
python3 scripts/smoke_test_radar_news_verification_sidecar.py
python3 scripts/smoke_test_shared_news_overlay.py
python3 scripts/check_radar_contract_cases.py
```

Integration checks that require private upstream services, credentials, browser
state, or generated production outputs should be run in the operator's private
runtime, not in this repository.
