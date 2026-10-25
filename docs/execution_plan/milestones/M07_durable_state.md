# M7: durable state, sealed vault and batch limits

**Source:** [ROADMAP.md](../../ROADMAP.md#phase-1-the-worklist-swarm) Phase 1 (durable state, cost cap, done when),
changed by decisions D9–D10. **Agents:** infra, api, qa. **Checkpoint:** [CP7](../HUMAN_QA.md#cp7-durable-state-and-batch-limits-outline).

Planned to the step; tasks written at CP6.

## What changes from the roadmap

The roadmap says resume means re-running a case, because the vault dies with the run. Decision D9 changes
that: the vault is **sealed per case**, so a saved graph state can be resumed after a restart. This reverses a
recorded design choice. The README's "never written to disk" wording needs a proposed change at CP7.

## Design: two stores

| Store | Holds | Access |
|---|---|---|
| Checkpoints (LangGraph Postgres checkpointer) | De-identified graph state only: placeholders and shifted dates. Every write passes the checkpoint guard from M5 | The API |
| Sealed vaults (own table) | One encrypted re-identification map per case: placeholder ↔ real value, and the date offset | Only the code that unseals for a resume or a rehydration |

- `Vault.seal(key) -> bytes` and `Vault.unseal(blob, key)` are the only way a vault leaves memory. `__reduce__`
  still refuses pickling, so an accidental serialization fails as it does today.
- Envelope encryption: one data key per case, wrapped by a master key (a local key file in dev; KMS in M12).
- Deletion is crypto-shredding: when a case is resolved (or its retention period ends), its data key is
  destroyed and the sealed blob becomes unreadable. The checkpoints may stay, because they hold nothing to
  re-identify.
- Rehydration for the reviewer happens only at the API edge, as today.

## Steps

| Step | Done when |
|---|---|
| Postgres in compose | A `db` service; migrations; in-memory mode still works for tests |
| Postgres checkpointer | Threads survive an API restart; the checkpoint guard runs on every write |
| Sealed vault | Seal, unseal, shred; tests that a checkpoint row never holds a vault value, that a shredded case cannot be rehydrated, and that a vault still cannot be pickled |
| Resume from the last node | On start, interrupted threads continue from their last checkpoint (a case waiting on a reviewer just keeps waiting), not from scratch |
| Per-batch cost cap | A batch stops starting cases when estimated spend reaches the cap; the stat card shows it |
| 50-case batch | Measured: cases per minute and cost per batch at the CP4 concurrency; fraction reaching the reviewer unflagged |

M12 may start in parallel once this checkpoint passes.
