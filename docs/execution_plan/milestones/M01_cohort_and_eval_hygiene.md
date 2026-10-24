# M1: bigger cohort, eval hygiene

**Source:** [ROADMAP.md](../../ROADMAP.md#phase-0-baseline-and-hygiene) Phase 0, all but the paid re-run (that is M3).
**Agents:** data, evals, qa. **Checkpoint:** [CP1](../HUMAN_QA.md#cp1-bigger-cohort-eval-hygiene).
**No live model spend.**

## Outcome

A cohort large enough that every eval situation has at least 5 cases, a frozen held-out split, and latency in
experiment summaries. All of it is checked with scripted models, so M3's paid run happens once, on a dataset
that will not change under it.

## Tasks

| Task | Agent | Depends on | Done when |
|---|---|---|---|
| M1.1 Size the cohort | data | — | A strata report over a trial cohort says how many patients it takes for 5 per situation (the roadmap's **verify**). The answer and the generation time go in the handoff |
| M1.2 Generate the cohort | data | M1.1 | `TARGET` raised in `data/generate.sh`, cohort generated, fingerprint recorded in the handoff. If one situation stays under 5 at a sane size, the handoff says so with counts |
| M1.3 Strata report | evals | — | A command prints cases per situation for the current cohort (used in M1.1 and at CP1) |
| M1.4 Latency in summaries | evals | — | `summarize` in `evals/experiment.py` carries per-node `seconds` (p50 and max) from `evals/run.py`. Test with scripted runs |
| M1.5 Held-out split | evals | M1.2 | A committed list of case keys, picked by fixed seed and stratified, that tuning never reads. `select_patients` takes `split="work"` or `"held_out"`; summaries report the two apart |
| M1.6 Cases per stratum | evals | M1.2, M1.5 | `CASES_PER_STRATUM` raised to 5 for the working split, with selection still including the smallest and the largest chart |
| M1.7 QA and packet | qa | all | Full test suite passes; a scripted-model experiment runs on both splits; CP1 packet assembled |

M1.3 and M1.4 run in parallel with M1.1 (different owners, different files). M1.5 waits for the cohort.

## Contracts

- Held-out file: `evals/held_out.txt`, one 8-character case key per line, sorted. It is the only place the
  split is defined.
- The evals agent decides how the split is stratified and writes the method at the top of the file as a comment.

## Risks

- Generation time. About a minute per patient, and RA is roughly 0.3% of the population, so a cohort of 150
  takes hours of Synthea batches. The data agent runs it in the background and reports progress; the
  orchestrator does not wait idle on it (M1.3 and M1.4 proceed).
- The `ra-not-active` situations are rare in Synthea's RA module and may need a cohort far larger than the
  others. CP1 decides whether to accept fewer than 5 there, documented.
- `evals/RESULTS.md` links issue #27. Per decision D5, either reopen it or leave the link as history.
