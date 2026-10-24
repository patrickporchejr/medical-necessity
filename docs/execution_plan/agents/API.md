# api agent

**Role:** the pipeline and the service around it: graph nodes, criteria schema, PHI gateway, case API,
dispatcher, decisions, persistence (with infra from M7).
**Active in:** M2, M4, M5, M7, M8, M9, M11.

## Owns

`api/app/` except `api/app/lab/`; `api/tests/` except the lab tests.

## Reads first

The README's "The PHI boundary" and "The graph" sections, `api/app/graph/build.py`, `api/app/phi/gateway.py`,
`api/app/routes/cases.py`, and the contracts in the current milestone file.

## Rules

- **The PHI boundary holds.** Every tool result passes through the gateway; nothing downstream sees a real id, name
  or date. A new source or tool gets a gateway test before it gets a node that uses it.
- **Contracts come from the orchestrator.** If one needs to change, stop and say so in the handoff; do not change
  it and tell the web agent afterwards.
- **Shared rules.** `support.py`, `criteria.py` and `state.py` are changed by one task at a time. Each change
  names what it does to ground truth.
- `verify` judges by rules, not by what a model says. Never relax a check to make a test or score pass.
- Bounded loops stay bounded (`MAX_REPAIRS`, the schema retry); new loops get a bound.
- One case failing never stops others (`_run` catches per case; keep that).
- Scripted models in tests; no live calls.

## Done means

`cd api && uv run pytest -q ../api ../evals` passes with new tests for the change, and the handoff shows any
API response that changed shape.
