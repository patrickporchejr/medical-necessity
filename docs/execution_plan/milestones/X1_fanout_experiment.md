# X1: per-criterion fan-out (experiment)

**Agents:** api, evals, qa. **Checkpoint:** [CPX1](../HUMAN_QA.md#cpx1-per-criterion-fan-out-experiment).
**Runs after CP3, alongside M4, and lands before M5** (M5 adds the review node to the same graph).
**Live model spend: needs your approval first.**

An experiment, not a feature: it ships only if it beats the M3 baseline. Losing is a valid result, and the
write-up reports it that way.

## Hypothesis

Drafting each criterion in its own subagent, holding only that criterion's evidence, and repairing only the
criteria `verify` flagged, cuts latency and repair tokens without lowering citation resolution.

## Design

- `assemble` fans out with LangGraph's `Send`: one subagent per criterion that needs a model (gaps still need none).
  A join node gathers the drafts into one packet.
- All subagents run inside one `run_pipeline` call, so they share that run's vault, gateway and prompt guard.
  The PHI boundary does not change.
- Repair re-sends only flagged criteria. `MAX_REPAIRS` still bounds the loop.
- Behind a setting (`assemble_mode = "single" | "fanout"`), so both modes run on the same commit.

## Measure

Both modes, both models, both splits, at M3's run count: citation resolution, accuracy, tokens and cost per
case, latency per node, and repair rate. Held-out cases are reported separately.

## Tasks

| Task | Agent | Done when |
|---|---|---|
| X1.1 Fan-out behind the setting | api | Scripted-model tests: same packet shape, per-criterion repair, the bound holds, no subagent sees another criterion's evidence |
| X1.2 Estimate | evals | Run count × cost; you approve |
| X1.3 Run and write up | evals | An X1 section in RESULTS.md with a keep or drop recommendation |
| X1.4 QA and packet | qa | Spot checks; packet assembled |

**Keep if:** citation resolution on the held-out set is no lower than baseline, *and* latency or cost improves by
more than run-to-run variance. Otherwise the setting stays at `single`, and the code is removed or kept off
(you decide at CPX1).
