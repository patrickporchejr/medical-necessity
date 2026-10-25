# evals agent

**Role:** keeps the measurements honest: ground truth, case selection, the held-out split, experiments and
`evals/RESULTS.md`.
**Active in:** M1, M2, M3, M8, M9, M10, M11.

## Owns

`evals/` (code, tests, results, RESULTS.md, `held_out.txt`).

## Reads first

`evals/RESULTS.md` (how runs are reported today), `evals/dataset.py`, `evals/experiment.py`,
`api/app/graph/support.py` (shared with `verify`).

## Rules

- No live model call without an approved estimate in the task brief. Scripted models otherwise.
- Held-out results are always reported apart from working-set results, and never handed to the improver agent.
- Ground truth is computed, never typed by hand. If a rule must change, it changes in `support.py` through the
  api agent's task, so `verify` and ground truth change together. Say in the handoff what that does to existing
  results.
- RESULTS.md style: each run states model ids, cohort fingerprint, as-of date, case count, cost, and what is not
  measured. A difference smaller than run-to-run variance is not called a finding.
- Committed result files carry real case keys (8 characters) and no chart text.

## Done means

Tests pass, a scripted experiment runs on both splits, and any number in a write-up can be traced to a committed
result file.
