# Public Release Checklist

This checklist must pass before changing the GitHub repository from private to
public.

## Repository Visibility

- Keep the repository private until every item below is complete.
- Enable GitHub secret scanning and push protection where available.
- Confirm the default branch only contains sanitized public-ready history.

## Secret Hygiene

GitHub secret scanning and push protection must be enabled before release.
Maintainers should run any operator-specific local scans outside this
repository, because private detection rules can themselves expose personal
infrastructure details if committed.

Review current files and reachable Git history for credentials, SSH material,
private hosts, private IP addresses, private ports, personal paths, or
deployment aliases. Any finding is a release blocker.

## Runtime Boundary

Confirm that the repository contains only public-safe defaults:

- No real API keys, tokens, cookies, browser storage state, SMTP credentials, or
  service-account files.
- No SSH keys, SSH config, known-host files, private hostnames, private IP
  addresses, private ports, jump-host details, or operator-specific aliases.
- No real private runtime roots, generated state, logs, caches, production
  database files, generated reports, or raw-news archives. Generic example
  paths such as `/opt/industry-signal-radar` are acceptable only when they are
  clearly documented as operator-replaceable templates and contain no hostname,
  IP address, SSH detail, account name, secret, or production output.
- No paid-source or proprietary raw data that cannot be redistributed.

Public examples must use placeholders or synthetic fixtures.

## Documentation and Governance

- `README.md` explains the public value proposition and security boundary.
- `LICENSE` is present and matches the intended open-source license.
- `SECURITY.md` explains private vulnerability reporting and the no-secrets
  issue policy.
- `CONTRIBUTING.md` explains development checks and data boundaries.
- Project-structure changes are reflected in `STATUS.md`.

## Functional Smoke Checks

Run the public-safe smoke checks:

```bash
python3 -m compileall -q scripts
python3 scripts/smoke_test_radar_candidate_pool.py
python3 scripts/smoke_test_shared_news_overlay.py
python3 scripts/smoke_test_radar_news_verification_sidecar.py
python3 scripts/check_radar_contract_cases.py
```

If an integration check requires private upstream services, document the skip and
run the closest synthetic fixture instead.

## Final Review

- Review the GitHub repository page while it is still private.
- Review Actions logs for accidental path, host, or token exposure.
- Check that issues, pull request templates, and examples do not invite users to
  paste secrets into public threads.
- Only switch visibility after the maintainers agree that both current content
  and reachable history are public-safe.
