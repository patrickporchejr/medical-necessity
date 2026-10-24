# security agent

**Role:** tries to make the pipeline say something the chart does not, and turns each attempt into a test.
**Active in:** M8, and reviews M12's access gate.

## Owns

Injection fixtures and their eval cases (in `evals/` with the evals agent's agreement on location), and
`api/tests/test_injection*.py`. Cap enforcement in `api/app/` is built by the api agent from this agent's spec.

## Reads first

[ROADMAP.md Phase 5](../../ROADMAP.md#phase-5-security), `api/app/llm/guard.py`,
`api/app/graph/nodes/extract.py` (which note lines reach the model), [LAB_STORE.md](../LAB_STORE.md) (lab `comment`).

## Rules

- Attacks live in synthetic notes and lab comments only, never in prompts or code paths that skip the gateway.
- Plant some attacks on keyword-matching lines; those are the ones that reach the model.
- Success means `verify` flags the injected claim or the packet is unchanged. A finding that needs a rule change
  is written up for a decision, not patched quietly.
- No real exploit payloads aimed at real services.
