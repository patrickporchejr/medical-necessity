# M9: second service line and criteria ingestion

**Source:** [ROADMAP.md](../../ROADMAP.md#phase-2-more-payers-and-service-lines-criteria-ingestion) Phase 2.
**Agents:** criteria, api, data, evals, qa. **Checkpoints:** CP9a (criteria approval), CP9b (score).

Planned to the outcome; the orchestrator writes tasks at CP8, starting with the roadmap's **verify** (which
Synthea module qualifies for a second line).

- Gate criterion and criteria file per case, not per process (`GATE` in `build.py`, `CRITERIA_FILE` in `main.py`
  and `evals/dataset.py`, `stratum_of`).
- Criteria schema extended where the second policy needs it (thresholds, inadequate response, end dates; closed
  issue #26), with `extract`, `support.py` and ground truth changed together.
- An ingestion step that proposes YAML in the existing schema from a synthetic or license-checked public policy,
  or flags what the schema cannot express. **CP9a:** you approve each proposal.
- A second cohort, and a lab mix for it if its policy needs screening labs.
- **CP9b:** the second line end to end on the M3 eval setup, held-out separate.

## Cohort lesson from M1

RA is about 0.3% of Synthea's population, and M1 needed roughly 100 batches of 500 for its rarest situation
(trial shown, no active-disease notes) to reach 5 cases. Two causes, and a different answer to each:

- **Prevalence.** Prefer a common condition (type 2 diabetes, asthma, obesity), so most of each batch is kept.
  The roadmap's **verify** measures prevalence in Synthea before the line is chosen.
- **Rare combinations.** Every policy has situations Synthea seldom writes (e.g. metformin tried for 3 months,
  A1c still above target), and a common condition does not fix that. Build those cases on purpose: take a
  generated chart and edit it into the situation, as M2.2 writes screening labs into the bundles. Each edit is a
  seeded, committed post-step, and the eval report lists which cases were edited.
