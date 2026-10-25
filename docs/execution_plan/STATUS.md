# Status

Kept by the orchestrator ([ORCHESTRATOR.md](ORCHESTRATOR.md)); agents do not edit it. Git wins when this and
the branches disagree.

## Now

**M0: awaiting CP0.** The plan is drafted. Next: you review it with [HUMAN_QA.md](HUMAN_QA.md#cp0-the-plan)
and answer decisions D1–D8.

## Milestones

| # | Milestone | State | Branch | Checkpoint |
|---|---|---|---|---|
| M0 | The plan | awaiting CP0 | — | CP0 |
| M1 | Bigger cohort, eval hygiene | not started | `m1/cohort` | CP1 |
| M2 | Reference lab | not started | `m2/reference-lab` | CP2 |
| M3 | Baseline run (Run 4) | not started | `m3/baseline` | CP3 |
| M4 | Run the worklist | not started | `m4/worklist` | CP4 |
| X1 | Per-criterion fan-out (experiment) | not started | `x1/fanout` | CPX1 |
| M5 | Lanes and decisions | not started | `m5/lanes` | CP5 |
| M6 | Reviewer UX | not started | `m6/reviewer-ux` | CP6 |
| M7 | Durable state and batch limits | not started | `m7/durable-state` | CP7 |
| M8 | Prompt-injection hardening | not started | `m8/security` | CP8 |
| M9 | Second service line | not started | `m9/second-line` | CP9a, CP9b |
| M10 | Learning from reviewers | not started | `m10/improver` | CP10 |
| M11 | Denials and appeals | not started | `m11/appeals` | CP11 |
| M12 | Deploy on AWS | not started | `m12/deploy` | CP12a, CP12b |
| M13 | The demo | not started | `m13/demo` | CP13 |

States: `not started`, `planning`, `in progress`, `awaiting CP<N>`, `passed`, `cut`.

## Open tasks

| Task | Agent | State | Since | Notes |
|---|---|---|---|---|
| — | | | | |

Task states: `dispatched`, `handed back`, `in QA`, `QA failed (n)`, `accepted`, `blocked`.

## Decisions

| Checkpoint | # | Decision | Answer | Date |
|---|---|---|---|---|
| CP0 | D1 | Reference lab as own service or in the FHIR server | | |
| CP0 | D2 | Screening mix | | |
| CP0 | D3 | Where positive screens land | | |
| CP0 | D4 | Eval coverage of screening situations | | |
| CP0 | D5 | Task tracking | | |
| CP0 | D6 | README change, when and by whom | | |
| CP0 | D7 | Scope | | |
| CP0 | D8 | Agent definitions | | |
| CP0 | D9 | Vault lifetime with saved graph state | Sealed per case: encrypted, stored apart from checkpoints, crypto-shredded at close | 2026-10-24 |
| CP0 | D10 | Reviewer decisions as graph interrupts | Yes | 2026-10-24 |
| CP0 | D11 | Per-criterion subagent fan-out | An eval-gated experiment (X1), kept only if it beats the baseline | 2026-10-24 |
| CP0 | D12 | Demo code in the repo | Yes, demo mode is product code; one-off recording helpers stay local | 2026-10-24 |
| CP0 | D13 | Demo layers | In-app demo mode and a click-through in a demo tool; no enterprise platforms | 2026-10-24 |
| CP0 | D14 | Click-through tool | Arcade: for LinkedIn video and GIF, not funnel analytics | 2026-10-24 |

## Checkpoint results

| Checkpoint | Result | Date | Follow-ups |
|---|---|---|---|
| CP0 | | | |

## Log

- 2026-10-24: plan drafted (M0).
- 2026-10-24: D9–D11 decided in discussion; X1 added, M5 and M7 rewritten for interrupts and the sealed vault.
- 2026-10-24: D12–D13 decided; M13 (the demo) and the demo agent added.
- 2026-10-24: D14 Arcade; M13 re-aimed at buyer and technical audiences, solution brief added.
