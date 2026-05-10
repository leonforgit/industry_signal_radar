<!-- codex-workspace-bootstrap:managed -->
# AGENTS.md

Applies to the whole repository. Use this file as the local operating manual for Codex work in this workspace.

## Repo Map

- `reports/main.md` is the canonical living main report for this project. Durable conclusions should merge back there instead of staying in scattered notes.
- `docs/` holds project plans, architecture notes, node definitions, workflow rules, and durable operating documents.
- `workpapers/` is the staging layer for external scans, source maps, experiment notes, backlog items, and unresolved questions.
- `data/` holds small canonical inputs, schemas, mappings, fixtures, and manifests. Do not turn this repo into a bulk raw-news warehouse.
- `output/` is for generated deliverables, previews, and exported summaries. Regenerate outputs rather than hand-editing them.
- `scripts/` contains stable entrypoints and pipeline helpers. Prefer wrappers over low-level helpers when both exist.

## Preferred Workflow

- Start from source-of-truth inputs, configs, plans, or notes before touching generated outputs.
- Use `workpapers/` to think and `reports/main.md` to conclude.
- If a future collector or pipeline creates large caches, keep them outside the repo and store only manifests, samples, or durable summaries here.
- If you add a smoke test later, prefer a stable wrapper under `scripts/` and run it after pipeline changes.

## Context Hygiene

- Keep runtime directories, preview renders, and browser traces out of the main working context unless debugging requires them.
- Regenerate derived outputs instead of manually editing them whenever a stable script already owns that output.
- Avoid loading full raw-news corpora, browser session dumps, or large scraped HTML archives into context unless the task is explicitly about ingestion debugging.

## Done Checklist

- The relevant validation command completed successfully.
- Generated artifacts were regenerated rather than hand-edited.
- Send, publish, or payment actions were only used intentionally and with explicit user approval where appropriate.
- Material project-structure changes were reflected in `README.md` and `STATUS.md`.
- Material research conclusions were merged into `reports/main.md`.
