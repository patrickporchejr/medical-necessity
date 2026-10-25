# M3: baseline run (Run 4)

**Source:** [ROADMAP.md](../../ROADMAP.md#phase-0-baseline-and-hygiene) Phase 0, done when. **Agents:** evals, qa.
**Checkpoint:** [CP3](../HUMAN_QA.md#cp3-baseline-run-run-4). **Live model spend: needs your approval first.**

## Outcome

One paid run, Haiku and Flash, on the M1 cohort with G1's splits, reported in `evals/RESULTS.md` as Run 4:
accuracy, citation resolution, cost and latency per model, for the working, held-out and hard splits apart.
Held-out is scored against G1's model labels and hard against G1's by-construction answers; the working split against computed ground truth, as a
wiring check. Rates come with counts ("19 of 20"). Every later milestone compares with this.

Expect lower numbers than Run 3, especially on the hard split. That is the point: Run 3 measured agreement with
the rules, Run 4 measures agreement with labels the code did not write. (Changed by D15: M2 is cut, so there is no lab; G1's labels are new.)

## What G1 settled

- **Splits:** working 32 cases (computed ground truth, wiring check), held-out 25 (G1's model labels), hard 24
  (`data/hard/cases.yaml`, built locally into `data/synthea/hard/`).
- **Hard split scoring (D20, D21, D23):** only the 30 targeted criteria count; the other 90 are reported apart as
  a wiring check. `evals/experiment.py` already does this (G1.8).
- **Unlabeled held-out labels (D22):** 10 of 125 are `unlabeled` (5 where the labeler's runs disagreed, all 5 of
  b8a1ffa3's). They are left out of held-out scoring, and the write-up says how many and why. b8a1ffa3 still
  runs through the pipeline: it is the largest chart and its cost and latency count.
- **As-of date:** held-out and hard runs must use the labels' `as_of`, 2026-09-20; `experiment.py` refuses any
  other `AS_OF_DATE` (G1.6b).
- **Token estimates:** in G1.4, characters / 4 undercounted tokens about 2x, because record ids are long. M3.1
  estimates from measured tokens (Run 3's per-run totals, scaled by the pipeline's actual prompt sizes on the
  new cohort, measured locally with scripted models), not from characters.

## Tasks

| Task | Agent | Depends on | Done when |
|---|---|---|---|
| M3.1 Estimate | evals | G1 | Case count × runs × 2 models × Run 3 cost per run, plus a margin; 3 runs on held-out and hard, 1 on working (D15). In the handoff. The orchestrator asks you before M3.2 |
| M3.2 Run | evals | your yes | A smoke run on two cases first (the largest held-out chart and one hard case), checked against the estimate; then both models on all three splits with `AS_OF_DATE=2026-09-20`, stopping if spend passes the approved cap; result files committed under `evals/results/run-4/` |
| M3.3 Write-up | evals | M3.2 | A Run 4 section in RESULTS.md: new baseline (independent labels and hard cases), tables per split with counts, every hard case the rules got wrong (Q1's input), whether Haiku's "no order" misses recur, latency per node |
| M3.4 README diff | orchestrator | M3.3 | A proposed README diff (D6), not applied; you approve it at CP3 |
| M3.5 QA and packet | qa | all | Spot-check two cases' citation resolution against their run files; packet assembled |

## Risks

- If a model's results swing widely between its 3 runs, the write-up says so and does not call the difference
  between models a finding.
- The splits are small. A difference of one or two cases is not a finding either.
- The held-out labels were written by a model and not reviewed by a human (D19). The write-up says so.
- The labeler (Sonnet 5.5) and Haiku are in the same family and may share mistakes; the hard split is the guard.
- Large charts (b8a1ffa3, ~870k tokens of chart) may make some pipeline calls much larger than Run 3's average.
  The smoke run includes it for that reason.
