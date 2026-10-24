# M3: baseline run (Run 4)

**Source:** [ROADMAP.md](../../ROADMAP.md#phase-0-baseline-and-hygiene) Phase 0, done when. **Agents:** evals, qa.
**Checkpoint:** [CP3](../HUMAN_QA.md#cp3-baseline-run-run-4). **Live model spend: needs your approval first.**

## Outcome

One paid run, Haiku and Flash, on the M1 cohort with the M2 lab, reported in `evals/RESULTS.md` as Run 4:
accuracy, citation resolution, cost and latency per model, held-out separate. Every later milestone compares
with this.

## Tasks

| Task | Agent | Depends on | Done when |
|---|---|---|---|
| M3.1 Estimate | evals | M2 | Case count × 3 runs × 2 models × Run 3 cost per run, plus a margin; in the handoff. The orchestrator asks you before M3.2 |
| M3.2 Run | evals | your yes | Both models run on both splits with pinned `AS_OF_DATE`; result files committed under `evals/results/run-4/` |
| M3.3 Write-up | evals | M3.2 | A Run 4 section in RESULTS.md: new baseline (the lab changed ground truth), tables per split, whether Haiku's "no order" misses recur, latency per node |
| M3.4 README diff | orchestrator | M3.3 | Only if D6 says so: a proposed diff, not applied |
| M3.5 QA and packet | qa | all | Spot-check two cases' citation resolution against their run files; packet assembled |

## Risks

- If a model's results swing widely between its 3 runs, the write-up says so and does not call the difference
  between models a finding.
