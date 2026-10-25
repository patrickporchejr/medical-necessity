# Status

Kept by the orchestrator ([ORCHESTRATOR.md](ORCHESTRATOR.md)); agents do not edit it. Git wins when this and
the branches disagree.

## Now

**M1: in progress** on `m1/cohort`. M1.3 and M1.4 accepted; M1.1 (size the cohort) running. No live model
spend in M1. Scope trimmed by D15 ([TRIMMED_ROADMAP.md](../TRIMMED_ROADMAP.md)): after M1 come G1, M3, Q1, D1.

## Milestones

| # | Milestone | State | Branch | Checkpoint |
|---|---|---|---|---|
| M0 | The plan | passed | — | CP0 |
| M1 | Bigger cohort, eval hygiene | in progress | `m1/cohort` | CP1 |
| G1 | Independent ground truth and hard cases | not started | `g1/ground-truth` | CPG1 |
| M3 | Baseline run (Run 4), on G1's labels | not started | `m3/baseline` | CP3 |
| Q1 | Faithful wording and fixes (Run 5) | not started | `q1/faithfulness` | CPQ1 |
| D1 | The demo, hosted | not started | `d1/demo` | CPD1 |
| M2 | Reference lab | cut (D15) | — | — |
| M4 | Run the worklist | cut (D15), slice kept in D1 | — | — |
| X1 | Per-criterion fan-out (experiment) | cut (D15) | — | — |
| M5 | Lanes and decisions | cut (D15), slice kept in D1 | — | — |
| M6 | Reviewer UX | cut (D15), slice kept in D1 | — | — |
| M7 | Durable state and batch limits | cut (D15) | — | — |
| M8 | Prompt-injection hardening | cut (D15), folded into G1 and Q1 | — | — |
| M9 | Second service line | cut (D15) | — | — |
| M10 | Learning from reviewers | cut (D15), its faithfulness metric moved to Q1 | — | — |
| M11 | Denials and appeals | cut (D15) | — | — |
| M12 | Deploy on AWS | cut (D15), slice kept in D1 | — | — |
| M13 | The demo | cut (D15), slice kept in D1 | — | — |

States: `not started`, `planning`, `in progress`, `awaiting CP<N>`, `passed`, `cut`.

## Open tasks

| Task | Agent | State | Since | Notes |
|---|---|---|---|---|
| M1.1 Size the cohort | data | dispatched | 2026-10-24 | Synthea in Docker, background |
| M1.3 Strata report | evals | accepted | 2026-10-24 | `uv run --project api python evals/strata.py [--fhir-dir DIR]` |
| M1.4 Latency in summaries | evals | accepted | 2026-10-24 | run.py now sums a node's laps across a repair |

Task states: `dispatched`, `handed back`, `in QA`, `QA failed (n)`, `accepted`, `blocked`.

## Decisions

| Checkpoint | # | Decision | Answer | Date |
|---|---|---|---|---|
| CP0 | D1 | Reference lab as own service or in the FHIR server | Own service (`lab`, port 8002) | 2026-10-24 |
| CP0 | D2 | Screening mix | As proposed in LAB_STORE.md | 2026-10-24 |
| CP0 | D3 | Where positive screens land | Resolve flagged claims, reason "screening positive" | 2026-10-24 |
| CP0 | D4 | Eval coverage of screening situations | Existing six strata, plus at least 5 cases per screening situation overall | 2026-10-24 |
| CP0 | D5 | Task tracking | STATUS.md only | 2026-10-24 |
| CP0 | D6 | README change, when and by whom | The orchestrator proposes a README diff at CP3; you approve | 2026-10-24 |
| CP0 | D7 | Scope | All of M1–M13 and X1; M11 first to cut. **Superseded by D15** | 2026-10-24 |
| CP0 | D8 | Agent definitions | Create `.claude/agents/` from the briefs; models as in ORCHESTRATOR.md | 2026-10-24 |
| CP0 | D9 | Vault lifetime with saved graph state | Sealed per case: encrypted, stored apart from checkpoints, crypto-shredded at close | 2026-10-24 |
| CP0 | D10 | Reviewer decisions as graph interrupts | Yes | 2026-10-24 |
| CP0 | D11 | Per-criterion subagent fan-out | An eval-gated experiment (X1), kept only if it beats the baseline | 2026-10-24 |
| CP0 | D12 | Demo code in the repo | Yes, demo mode is product code; one-off recording helpers stay local | 2026-10-24 |
| CP0 | D13 | Demo layers | In-app demo mode and a click-through in a demo tool; no enterprise platforms | 2026-10-24 |
| CP0 | D14 | Click-through tool | Arcade: for LinkedIn video and GIF, not funnel analytics | 2026-10-24 |
| M1 | D15 | Scope, replacing D7 | [TRIMMED_ROADMAP.md](../TRIMMED_ROADMAP.md): M1, then G1, M3, Q1, D1; all else cut. D1–D3, D9 and D10 lapse with M2, M5 and M7; D11 with X1. Minimize spend: cheap models, estimates first, a free static host | 2026-10-24 |

## Workflow

What each milestone's agents did, what went wrong and who caught it, and what you decided that no agent
proposed. Evidence for the workflow write-up in D1. A few lines per milestone, added at its checkpoint.

| Milestone | Agents | Went wrong, caught by | Your call |
|---|---|---|---|
| M1 (so far) | data (opus): M1.1 trial cohort; evals (opus, ~103k tokens): M1.3, M1.4 | The trial was set to 150 batches (~2.5h); the orchestrator surfaced it and you capped it at 5 per occurring situation. The cohort had 34 bundles for `TARGET=30`; the orchestrator asked the data agent to explain. M1.4 found run.py kept only a node's last lap on a repair; the evals agent fixed it with a test | Cap the trial; record the cohort lesson in M9; trim scope (D15) |

## Checkpoint results

| Checkpoint | Result | Date | Follow-ups |
|---|---|---|---|
| CP0 | Passed (PR #53 merged) | 2026-10-24 | — |

## Log

- 2026-10-24: plan drafted (M0).
- 2026-10-24: D9–D11 decided in discussion; X1 added, M5 and M7 rewritten for interrupts and the sealed vault.
- 2026-10-24: D12–D13 decided; M13 (the demo) and the demo agent added.
- 2026-10-24: D14 Arcade; M13 re-aimed at buyer and technical audiences, solution brief added.
- 2026-10-24: D1–D8 set to the recommendations; agent definitions drafted (M0.3).
- 2026-10-24: CP0 passed (PR #53 merged). M1 opened; M1.1, M1.3, M1.4 dispatched.
- 2026-10-24: M1.3 and M1.4 accepted (51 evals tests pass). Under-5 is judged over situations that occur; absent ones listed apart (D4).
- 2026-10-24: D15 trims the scope (TRIMMED_ROADMAP.md, approved). G1, Q1, D1 added; M2 and M4–M13 and X1 cut; Workflow table started.
