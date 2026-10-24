# M7: durable state and batch limits

**Source:** [ROADMAP.md](../../ROADMAP.md#phase-1-the-worklist-swarm) Phase 1 (durable state, cost cap, done when).
**Agents:** infra, api, qa. **Checkpoint:** [CP7](../HUMAN_QA.md#cp7-durable-state-and-batch-limits-outline).

Planned to the step; tasks written at CP6.

| Step | Done when |
|---|---|
| Postgres in compose | A `db` service; migrations; the API starts against it and in-memory still works for tests |
| Cases, decisions and audit persisted | They survive an API restart. The packet records the change the roadmap names: the database now holds rehydrated packets with real ids (synthetic) |
| Resume | On start, cases `queued` or `running` are re-run from the start (the vault does not survive a run, by design) |
| Per-batch cost cap | A batch stops starting cases when estimated spend reaches the cap; the stat card shows it |
| 50-case batch | Measured: cases per minute and cost per batch at the CP4 concurrency; fraction reaching the reviewer unflagged |

M12 may start in parallel once this checkpoint passes.
