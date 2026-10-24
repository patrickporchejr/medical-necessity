# M5: lanes and decisions

**Source:** [WORKLIST_UI.md](../../WORKLIST_UI.md#build-steps) steps 4–5, [ROADMAP.md](../../ROADMAP.md#phase-1-the-worklist-swarm)
Phase 1 (reviewer actions and the audit record). **Agents:** api, web, data, qa.
**Checkpoint:** [CP5](../HUMAN_QA.md#cp5-lanes-and-decisions).

## Outcome

Every finished case is in exactly one lane. The reviewer can take every lane's decision, and each decision
**resumes the case's graph** rather than setting a flag on a finished case. Requesting records for a pending-lab
patient pauses the case until the lab releases the result; the case then resumes and checks the screening
criteria again.

## Design: decisions are graph interrupts

Decided before CP0 (STATUS.md, decisions D9–D10). The graph gains a `review` node after `verify` that calls
LangGraph's `interrupt()`. The case's thread pauses there, with its state saved by a checkpointer. A decision
is `Command(resume=decision)` on that thread:

| Decision | What the graph does next |
|---|---|
| `sign` (attestation required) | Finalize; the case is resolved |
| `override` (note required), `mark_gap` | Records the verdict in state; back to `review` if other criteria still need a decision |
| `request_records` | `await_records` node: interrupts again until the lab releases the result, then re-runs `extract` and `verify` **for the screening criteria only**, then `review` |
| `close`, `return` | End, resolved |

- The thread is the case's memory: what the pipeline did, plus every reviewer decision and its reason. The
  audit log is a projection of it.
- **The vault problem:** a thread that waits on a reviewer outlives the run's vault. Until M7 seals the vault
  per case, M5 keeps the vault in memory for the thread's life (a per-case vault held by the API, with the
  TTL raised), and uses LangGraph's in-memory checkpointer. An API restart loses waiting cases, and the CP5
  packet says so. M7 makes both durable.
- A **checkpoint guard** is built here, not in M7: every checkpoint write runs the prompt guard's
  known-value check and refuses a write that holds a value the vault knows. It is cheap now and matters once
  checkpoints hit disk.

## Contracts

- `lane` on each case: `flagged | ready_to_sign | missing_records | not_eligible | failed | resolved | null`,
  derived in one function from status, route, `verify`'s verdicts and decisions. A positive screen goes where
  D3 decides (recommended: `flagged`, reason "screening positive").
- `POST /cases/{id}/decisions {action, note?, attest?}` resumes the thread. Validation (note, attestation)
  happens before the resume. `GET /audit` lists decisions with time, action, note, reviewer `"reviewer"`.
- `request_records` asks the lab's `release_pending` for that patient (the demo control), and the thread waits.
  For a patient with nothing pending, nothing arrives, and the case says so.
- Bulk: `POST /cases/decisions {case_ids, action}` for `request_records` and re-run only.

## Tasks

| Task | Agent | Depends on | Done when |
|---|---|---|---|
| M5.1 Lane function | api | contracts | One function with a test per lane, including a positive screen and an unsupported gap (a flag, not missing) |
| M5.2 Review queue and Needs-you column | web | M5.1 | Lane counts, filter, removable chip, bulk action bar |
| M5.3 Per-case vault and checkpointer | api | — | The case's vault lives for its thread; in-memory checkpointer; checkpoint guard with a test that a planted real value is refused |
| M5.4 review and await_records nodes | api | M5.3 | `interrupt()` and resume for every action; scripted-model tests for each path; bounds on every loop |
| M5.5 Decisions endpoint and audit | api | M5.4 | Endpoints above; the audit log built from thread state |
| M5.6 Records arrive | api, data | M5.4, M2.3 | `request_records` → `release_pending` → resume → lab evidence; tested end to end |
| M5.7 Decision panel (interim) | web | M5.5 | Actions on the existing case page; "waiting on records" and "nothing arrived" states. The panel moves into the pane in M6 |
| M5.8 QA and packet | qa | all | Suite, build, checklist, CP5 dry-run |
