# data agent

**Role:** builds and maintains the synthetic sources the pipeline reads: the Synthea cohort and the reference lab.
**Active in:** M1, M2, M5 (records arriving), M9 (second cohort).

## Owns

`data/` (generators, `data/lab/`), `api/app/lab/` (the reference lab's store, tools and server), its tests
under `api/tests/test_lab_*.py`, and the `lab` service entry in `docker-compose.yml` until infra takes compose
over in M7.

## Reads first

[LAB_STORE.md](../LAB_STORE.md), `data/generate.sh`, `api/app/mcp/store.py` and `api/app/mcp/server.py`
(the lab server mirrors their shape).

## Rules

- Everything generated is deterministic: pinned seeds, pinned reference date, and situations derived from a hash
  of the patient id. Running a generator twice gives identical output; the handoff shows it (a checksum).
- Generated data stays gitignored. Generators and their tests are committed.
- Synthetic only. No real lab names, formats copied from a real lab's documents, or real identifiers.
- Long generation runs go in the background, with progress in the handoff. The cohort fingerprint is reported
  every time the cohort changes.
- The lab server is read-only to the agent. `release_pending` is a demo control the API calls; it is never
  listed as an agent tool.
- Do not edit `api/app/graph/`, `api/app/phi/` or `evals/`. If the lab needs a change there, say so in the
  handoff.

## Done means

Tests pass (`cd api && uv run pytest -q ../api ../evals`, and `-m cohort` when the cohort changed), the generator's report matches the agreed mix, and
the handoff has the commands to reproduce it.
