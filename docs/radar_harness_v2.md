# Radar Harness v2

Radar Harness v2 separates production control from research uncertainty and maintenance debt. The goal is to make the daily battlecard publish path deterministic while still preserving useful warnings for follow-up work.

The frozen contract and current findings ledger live in [radar_harness_contract_v1.md](radar_harness_contract_v1.md). Update that ledger before reopening old review findings.

## Status Dimensions

- `production_status`: publish safety. A failure here blocks the daily battlecard.
- `research_status`: model or research uncertainty. This can be `warn` while production remains publishable.
- `maintenance_status`: non-blocking source, enrichment, or hygiene backlog.
- `delivery_status`: email/send path health, written by the delivery wrapper after the workspace build.

The legacy top-level `status` remains for compatibility. It is `fail` only when the production dimension fails.

## Node Contracts

Each workspace step has a contract in `scripts/build_radar_workspace_outputs.py`.

- Core build, source readiness, price/fundamental freshness, report rendering, quality, and publish nodes are production nodes.
- Kimi research is a production-required research agent node: `partial_pass`, deterministic fallback, credential failure, or output `fail` blocks publish.
- Kimi risk flags are research warnings, not production failures.
- Company enrichment warnings are maintenance warnings unless the script command itself fails.
- Agent task queue output can be `action_required` without making the harness fail; the queue is an output of the harness, not a blocker for the report.

## Publish Gate

`publish_daily_battlecard_aliases` still requires:

- quality status is `pass`;
- snapshot, quality, and rendered report share the same `run_id`;
- all required alias source files exist.

The workspace harness now aborts earlier when a production-required node returns a failed output status even if the script exits `0`.

## Agent Task Queue

`scripts/build_radar_agent_task_queue.py` consumes production failures and production warnings from the v2 manifest as P1/P2 repair tasks. Research and maintenance warnings become P3 backlog tasks: visible and claimable, but not production blockers.

The queue has a minimal executable state machine:

- default run rebuilds the queue from health artifacts;
- `--claim TASK_ID --agent AGENT` leases a ready task;
- `--start TASK_ID --agent AGENT` marks claimed work as running;
- `--complete TASK_ID --agent AGENT --review-verdict pass|fail|needs_retry` runs the task `validation_commands`, writes return codes and log tails into the result artifact, and only allows `done` when every validation command exits `0`;
- `--block` and `--release` handle blocked or abandoned work.

Queue state updates are protected by `state/radar_agent_task_queue.lock`. Each rebuild also checks `lease_expires_at`; expired `claimed` or `running` tasks are returned to `ready` when attempts remain, or marked `blocked` when attempts are exhausted.

`--complete` is intentionally two-phase: it records `validating` and extends the task lease while holding the queue lock, releases the lock before running validation commands, then reacquires the lock and applies the result only if the task identity still matches. This avoids deadlocks when a validation command rebuilds the workspace and re-enters `build_radar_agent_task_queue.py`, while keeping successful long validations from being reclaimed before apply.

For isolated tests or alternate operators, `RADAR_AGENT_TASK_QUEUE_JSON`, `RADAR_AGENT_TASK_QUEUE_MD`, and `RADAR_AGENT_TASK_QUEUE_LOCK` can redirect the queue state files.

Research warnings are grouped by harness label before task creation so the queue points to a small number of executable repair jobs instead of one ticket per raw warning.

The email wrapper refreshes the task queue after writing delivery health so stale delivery failures do not survive a successful dry run or send.

The wrapper also runs a bounded auto-worker for ready P2/P3 tasks by default. The auto-worker only claims tasks that already have repair and validation commands, records repair/validation output in the result artifact, and leaves failed validations blocked for human/Codex review. If any P0/P1 task is still active, generic lower-priority execution is skipped; tasks explicitly marked as root-cause repairs with `auto_run_with_active_blockers` may still run because they are expected to clear the derived blocker. Operators can tune it with `RADAR_AGENT_TASK_AUTORUN`, `RADAR_AGENT_TASK_AUTORUN_MAX`, and `RADAR_AGENT_TASK_AUTORUN_PRIORITIES`.

Delivery health is split into `radar_daily_email_delivery_latest.json` and `radar_daily_email_delivery_scheduled_latest.json`. A later manual rerun may update the general latest file, but it no longer erases the latest scheduled timer result; the task queue will still surface a failed scheduled invocation.

## Kimi Sidecar Coverage

Kimi research payloads include `sidecar_coverage` for IPO and structural verdicts. The validator requires sidecar verdict shape, provenance, executable next actions, and required coverage before a `pass` payload can pass validation.

## Smoke Test

Run:

```bash
python3 scripts/smoke_test_radar_harness_v2.py
```

This protects the status semantics that have caused repeated false red lights:

- Kimi risk flags are research warnings only.
- Kimi `partial_pass` is a production failure.
- company enrichment long-tail gaps are maintenance warnings.
- task queue `action_required` is not self-blocking.
- task completion cannot reach `done` unless validation commands pass.
- expired leases are reclaimed.
- validation leases are extended while `--complete` is running.
- repair commands count toward completion leases and auto-worker parent timeouts.
- CLI completion can run validation commands that re-enter the queue builder without deadlocking.
- the bounded auto-worker can claim and complete a ready P3 task through the same CLI state machine.
- the bounded auto-worker can run an allowed root-cause P2 task while a derived P0 blocker is active.
- non-critical upstream source outages turn source readiness to `warn`, not a clean `pass`.
- current artifact health can recover a stale manifest fail to the current warn/pass state while preserving workspace audit fields.
- current artifact health honors explicit `warning_count` even when `warnings` is empty.
- current artifact health fails malformed critical artifacts with missing status, explicit blockers, or explicit failures.
- Kimi sidecar coverage cannot be forged with empty `ipo_verdicts` or `structural_verdicts`.
- Kimi sidecar coverage cannot hide available sidecar objects by setting `required_count=0`.
- market-sample freshness uses a configured `market_sample_ready_time` cutoff so morning or pre-cutoff runs do not demand an unavailable same-day close.
- Kimi model nodes have workspace-level command timeouts, so a stalled model call becomes an explicit failed step rather than an invisible hung build.
- Kimi HTTP calls run inside hard-timeout child processes, while socket timeout uses the full model budget rather than a fixed 30 seconds.
- Kimi research writes a progress manifest for candidate shards and sidecar stages.
- IPO sidecar coverage normalizes exchange suffixes such as `.HK` and `.SZ`, and HK IPO watchlists exclude ETF listings from stock subscription research.
- Data substrate audit and email delivery gates treat non-blocking source/price warnings as warnings, not production blockers.
