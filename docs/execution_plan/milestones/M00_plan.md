# M0: the plan

**Owner:** orchestrator. **Checkpoint:** [CP0](../HUMAN_QA.md#cp0-the-plan).

## Tasks

| Task | Agent | Done when |
|---|---|---|
| M0.1 Write `docs/execution_plan/` | orchestrator | README, ORCHESTRATOR, STATUS, HUMAN_QA, LAB_STORE, milestones and agent briefs exist and link up |
| M0.2 Apply CP0 decisions | orchestrator | Every D1–D8 answer is reflected in the plan and logged in STATUS.md |
| M0.3 Agent definitions | orchestrator | `.claude/agents/<name>.md` for the agents M1 and M2 need (data, evals, api, web, qa), approved by you |
| M0.4 Open M1 | orchestrator | Branch `m1/cohort` exists, M1 marked `in progress` |

M0.2 to M0.4 happen only after CP0 passes.

## Notes

- The plan is uncommitted until you say where it goes: a `m0/plan` branch and PR (recommended), or straight to
  `main`.
- The existing docs (ROADMAP.md, WORKLIST_UI.md) are not edited. This plan links to them; where it differs (the
  lab store, the order), the difference is stated here.
