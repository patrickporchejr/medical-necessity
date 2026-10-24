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
