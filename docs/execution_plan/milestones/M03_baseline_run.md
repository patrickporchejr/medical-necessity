# M3: baseline run (Run 4)

**Source:** [ROADMAP.md](../../ROADMAP.md#phase-0-baseline-and-hygiene) Phase 0, done when. **Agents:** evals, qa.
**Checkpoint:** [CP3](../HUMAN_QA.md#cp3-baseline-run-run-4). **Live model spend: needs your approval first.**

## Outcome

One paid run, Haiku and Flash, on the M1 cohort with G1's splits, reported in `evals/RESULTS.md` as Run 4:
accuracy, citation resolution, cost and latency per model, for the working, held-out and hard splits apart.
Held-out and hard are scored against G1's hand labels; the working split against computed ground truth, as a
wiring check. Rates come with counts ("19 of 20"). Every later milestone compares with this.

Expect lower numbers than Run 3, especially on the hard split. That is the point: Run 3 measured agreement with
the rules, Run 4 measures correctness. (Changed by D15: M2 is cut, so there is no lab; G1's labels are new.)

## Tasks

| Task | Agent | Depends on | Done when |
|---|---|---|---|
| M3.1 Estimate | evals | G1 | Case count × runs × 2 models × Run 3 cost per run, plus a margin; 3 runs on held-out and hard, 1 on working (D15). In the handoff. The orchestrator asks you before M3.2 |
| M3.2 Run | evals | your yes | Both models run on all three splits with pinned `AS_OF_DATE`; result files committed under `evals/results/run-4/` |
| M3.3 Write-up | evals | M3.2 | A Run 4 section in RESULTS.md: new baseline (independent labels and hard cases), tables per split with counts, every hard case the rules got wrong (Q1's input), whether Haiku's "no order" misses recur, latency per node |
| M3.4 README diff | orchestrator | M3.3 | A proposed README diff (D6), not applied; you approve it at CP3 |
| M3.5 QA and packet | qa | all | Spot-check two cases' citation resolution against their run files; packet assembled |

## Risks

- If a model's results swing widely between its 3 runs, the write-up says so and does not call the difference
  between models a finding.
- The splits are small. A difference of one or two cases is not a finding either.
- The labels have one author, who also designed the rules. The write-up says so.
