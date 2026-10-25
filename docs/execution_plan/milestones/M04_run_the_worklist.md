# M4: run the worklist

**Source:** [WORKLIST_UI.md](../../WORKLIST_UI.md#build-steps) steps 1–3, [ROADMAP.md](../../ROADMAP.md#phase-1-the-worklist-swarm)
Phase 1 (dispatcher, concurrency). **Agents:** api, web, qa. **Checkpoint:** [CP4](../HUMAN_QA.md#cp4-run-the-worklist).

## Outcome

One click runs the whole cohort; the table and stat cards follow every case without opening it.

## Contracts

- `GET /cases` adds `summary` per case: `{node, nodes_done, nodes_expected, criteria: {<id>: {kind, supported|null}},
  route, repairs, cost_usd}`. Metadata and verdicts only, no assertion text.
- `POST /cases/batch {patient_ids?: [...]}` → the created cases. `POST /worklist/pause`, `POST /worklist/resume`,
  `GET /worklist` → `{state, running, queued, max_running}`. `POST /cases/{id}/rerun`.
- `MAX_RUNNING` becomes the setting `max_running`.

## Tasks

| Task | Agent | Depends on | Done when |
|---|---|---|---|
| M4.1 Tokens and shell | web | — | WORKLIST_UI step 1: tokens light and dark, Plus Jakarta Sans, fixed header, two scroll panes, phone breakpoint; existing pages work inside it |
| M4.2 List summary | api | contracts | `summary` on `GET /cases`, updated from `PipelineEvent`s; tests per node transition |
| M4.3 Cases table | web | M4.1, M4.2 | Table, tabs, search from `summary`; polling 1.5s busy and 5s idle; rows never reorder during a run |
| M4.4 Batch, pause, resume, rerun | api | M4.2 | Endpoints above; pause lets running cases finish; a failed case re-runs; retries with backoff on provider errors |
| M4.5 Concurrency measurement | api | M4.4 | Cases per minute and MCP tool latency at `max_running` 2, 8, 25 with scripted models and an artificial model delay; where the 8-chart LRU thrashes. Also, scripted results identical to sequential runs |
| M4.6 Worklist controls and stat cards | web | M4.3, M4.4 | Start, Pause, Resume, Run complete; four stat cards |
| M4.7 QA and packet | qa | all | Suite, `next build`, WORKLIST_UI checklist at desktop and 375px, CP4 dry-run |

M4.1 and M4.2 run in parallel from the start.
