# M5: lanes and decisions

**Source:** [WORKLIST_UI.md](../../WORKLIST_UI.md#build-steps) steps 4–5, [ROADMAP.md](../../ROADMAP.md#phase-1-the-worklist-swarm)
Phase 1 (reviewer actions and the audit record). **Agents:** api, web, data, qa.
**Checkpoint:** [CP5](../HUMAN_QA.md#cp5-lanes-and-decisions).

## Outcome

Every finished case is in exactly one lane; the reviewer can take every lane's decision; requesting records
for a pending-lab patient brings the result in, and the re-run moves the case on.

## Contracts

- `lane` on each case: `flagged | ready_to_sign | missing_records | not_eligible | failed | resolved | null`,
  derived in one function from status, route, `verify`'s verdicts and decisions. A positive screen goes where
  D3 says.
- `POST /cases/{id}/decisions {action, note?, attest?}`. Actions: `mark_gap`, `override` (note required),
  `request_records`, `close`, `return`, `sign` (attestation required). Append-only, timestamped, reviewer
  `"reviewer"` until there is auth. `GET /audit` lists them.
- `request_records` calls the lab's `release_pending` for that patient (the demo control), then marks the case
  `waiting_on_records`. A re-run picks up whatever was released. For a patient with nothing pending, nothing
  arrives, and the case says so.
- Bulk: `POST /cases/decisions {case_ids, action}` for `request_records` and re-run only.

## Tasks

| Task | Agent | Depends on | Done when |
|---|---|---|---|
| M5.1 Lane function | api | contracts | One function with a test per lane, including a positive screen and an unsupported gap (a flag, not missing) |
| M5.2 Review queue and Needs-you column | web | M5.1 | Lane counts, filter, removable chip, bulk action bar |
| M5.3 Decisions and audit | api | M5.1 | Endpoints above, in memory; decisions change lanes; validation for note and attestation |
| M5.4 Records arrive | api, data | M5.3, M2.3 | `request_records` → `release_pending` → re-run → lab evidence. Tested end to end with scripted models |
| M5.5 Decision panel (interim) | web | M5.3 | Actions on the existing case page; "records requested" and "nothing arrived" states. The panel moves into the pane in M6 |
| M5.6 QA and packet | qa | all | Suite, build, checklist, CP5 dry-run |

## Note

Decisions are in memory until M7. A page reload keeps them; an API restart does not, and the CP5 packet says so.
