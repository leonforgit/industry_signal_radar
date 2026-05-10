# Execution Checklist

This is the public-safe execution checklist for `Industry Signal Radar`.

The historical private runtime log has been intentionally removed from the
public repository. Operators should keep machine-specific deployment records,
hostnames, ports, SSH details, credentials, generated reports, health snapshots,
and production databases in private operations notes outside Git.

## Public-Safe Local Checks

Run these from the repository root:

```bash
python3 -m compileall -q scripts
python3 scripts/smoke_test_radar_candidate_pool.py
python3 scripts/smoke_test_radar_harness_v2.py
python3 scripts/smoke_test_radar_news_verification_sidecar.py
python3 scripts/smoke_test_shared_news_overlay.py
python3 scripts/check_radar_contract_cases.py
```

## Runtime Boundary

- Keep real `.env` files, API keys, SMTP credentials, SSH material, cookies,
  browser storage state, production databases, generated reports, and raw
  upstream payloads outside the repository.
- Treat `/opt/industry-signal-radar`, `/opt/news-event-hub`, and
  `/opt/quant-runtime` as public example path conventions only. Operators may
  replace them with any private runtime root through environment variables or
  private config overrides.
- Do not open public issues with private hostnames, private IP addresses,
  private ports, jump-host details, personal paths, or production output
  excerpts.

## Minimal Local Flow

1. Create a Python environment and install dependencies from
   `requirements.txt`.
2. Run the public-safe smoke checks above.
3. Use synthetic fixtures or your own public-safe event exports to test
   candidate and report generation.
4. Only enable optional integrations after configuring credentials in a private
   runtime environment.

## Release Gate

Before changing visibility, publishing a release, or accepting a security-
sensitive change:

- Confirm `git status --short --branch` is clean.
- Confirm local `HEAD` matches `origin/main`.
- Run smoke checks and any maintainer-private release scans outside this
  repository.
- Review GitHub Actions results.
- Confirm reachable history contains no credentials, SSH material, personal
  paths, private hostnames, private IP addresses, private ports, or runtime
  artifacts.
