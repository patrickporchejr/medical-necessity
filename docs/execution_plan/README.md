# Execution plan

Status: CP0 passed; scope trimmed by D15 on 2026-10-24. This plan turns [ROADMAP.md](../ROADMAP.md) into an
ordered sequence of milestones, carried out by one orchestrator agent and a small set of subagents, with a
human QA checkpoint at the end of every milestone. [TRIMMED_ROADMAP.md](../TRIMMED_ROADMAP.md) says which
milestones still run and why; the cut ones keep their files, marked `cut` in [STATUS.md](STATUS.md).

## How to read this

Start here, then go only as deep as you need:

| Level | Read | For |
|---|---|---|
| 1 | This page | The sequence, the checkpoints, who does what |
| 1 | [TRIMMED_ROADMAP.md](../TRIMMED_ROADMAP.md) | What the project is for, what runs, what was cut and why |
| 2 | [HUMAN_QA.md](HUMAN_QA.md) | What you test at each checkpoint and what you decide |
| 2 | [ORCHESTRATOR.md](ORCHESTRATOR.md) | How the work is dispatched, tracked and gated |
| 2 | [STATUS.md](STATUS.md) | Where the work is right now |
| 3 | [milestones/](milestones/) | One file per milestone: tasks, owners, order, done-when |
| 3 | [agents/](agents/) | One brief per subagent: scope, files it owns, rules |
| 3 | [LAB_STORE.md](LAB_STORE.md) | Design of the reference lab (M2, cut) |

The orchestrator reads levels 1 and 2 at the start of every session, and a milestone file when that
milestone starts. A subagent is handed its own brief and one task from one milestone file, not the whole plan.

## The sequence

Each milestone ends at a checkpoint (CP). Work does not move past a checkpoint until you mark it passed in
[STATUS.md](STATUS.md).

| # | Milestone | Source | Main agents | Checkpoint |
|---|---|---|---|---|
| M0 | [This plan](milestones/M00_plan.md) | — | orchestrator | **CP0** review the plan, answer the open decisions |
| M1 | [Bigger cohort, eval hygiene](milestones/M01_cohort_and_eval_hygiene.md) | Roadmap Phase 0 | data, evals | **CP1** strata counts, held-out split frozen |
| G1 | Independent ground truth and hard cases | New | evals, data | **CPG1** hand labels, hard split, every disagreement explained |
| M3 | [Baseline run (Run 4)](milestones/M03_baseline_run.md) | Roadmap Phase 0 | evals | **CP3** accept the new baseline numbers |
| Q1 | Faithful wording and the fixes Run 4 calls for (Run 5) | New, metric from M10 | api, evals | **CPQ1** Run 5 beside Run 4, every fix linked to a failed case |
| D1 | The demo, hosted | M4–M6, M12, M13, cut down | web, api, demo | **CPD1** a stranger opens the link; brief and workflow write-up in the README |

Cut by D15, files kept: [M2](milestones/M02_reference_lab.md), [M4](milestones/M04_run_the_worklist.md),
[X1](milestones/X1_fanout_experiment.md), [M5](milestones/M05_lanes_and_decisions.md),
[M6](milestones/M06_reviewer_ux.md), [M7](milestones/M07_durable_state.md), [M8](milestones/M08_security.md),
[M9](milestones/M09_second_service_line.md), [M10](milestones/M10_learning_from_reviewers.md),
[M11](milestones/M11_denials_and_appeals.md), [M12](milestones/M12_deploy.md), [M13](milestones/M13_demo.md).
G1, Q1 and D1 get their milestone files at the checkpoint before each.

Why this order:

- **Independent labels (G1) come before the paid baseline (M3).** Run 3's 1.000 was scored against ground
  truth computed with the pipeline's own rules, so it checked wiring, not correctness. Paying for Run 4 on the
  same labels would repeat that.
- **G1 changes no rules**, so Run 4 measures today's pipeline honestly, on the hard cases too (negation,
  conflicting notes, screening results, prompt injection).
- **Q1 comes after Run 4** because its fixes are chosen from what Run 4 got wrong, each justified by a failed
  case and scored against hand labels. Its quote check changes `assemble` and `verify`, so it cannot go before
  the baseline either.
- **The demo (D1) comes last** so it shows Run 5's numbers, and records output from the pipeline as it will
  stay. It is recorded only, so hosting it costs nothing per visit.
- **Spend is minimal throughout:** two paid runs (Run 4, Run 5) on the cheapest models, each estimated and
  approved first, and a free static host for the demo.

Detail matches distance: M1 and M3 are planned to the task; G1, Q1 and D1 to the outcome in
[TRIMMED_ROADMAP.md](../TRIMMED_ROADMAP.md). The orchestrator writes the detailed plan for a milestone at the
checkpoint before it, when what was learned is known. Each detailed plan is reviewed at that checkpoint.

## Who does what

| Agent | Brief | Owns |
|---|---|---|
| Orchestrator | [ORCHESTRATOR.md](ORCHESTRATOR.md) | The sequence, dispatch, STATUS.md, checkpoint packets. Writes no product code |
| data | [agents/DATA.md](agents/DATA.md) | Cohort generation, the reference lab's data and server |
| evals | [agents/EVALS.md](agents/EVALS.md) | Ground truth, experiments, RESULTS.md |
| api | [agents/API.md](agents/API.md) | The graph, PHI gateway, case API, dispatcher, persistence |
| web | [agents/WEB.md](agents/WEB.md) | The reviewer dashboard |
| qa | [agents/QA.md](agents/QA.md) | Verifies every task before it is accepted, and assembles the checkpoint packet |
| security | [agents/SECURITY.md](agents/SECURITY.md) | Not used after D15: injection cases moved to G1 (evals) |
| criteria | [agents/CRITERIA.md](agents/CRITERIA.md) | Not used after D15 (M9 cut) |
| improver | [agents/IMPROVER.md](agents/IMPROVER.md) | Not used after D15 (M10 cut) |
| infra | [agents/INFRA.md](agents/INFRA.md) | Not used after D15; D1's static hosting is web's |
| demo | [agents/DEMO.md](agents/DEMO.md) | Solution brief and click-through copy (D1) |

Agents are added when a milestone needs them, in line with the roadmap's first rule. After D15, data, evals,
api, web, qa and demo are the only ones with work.

## Rules every agent follows

From the roadmap, and from how this repo has been run:

1. **Synthetic data only.** No PHI in the repo, a prompt, a trace or a deployment.
2. **No score without an eval behind it.** Held-out cases are reported apart from tuning cases.
3. **Humans approve consequential changes.** Agents open PRs; you merge. Nothing past a checkpoint without you.
4. **No live model spend or cloud resource without an estimate and a yes.** Scripted models by default.
5. **No README or existing doc edits unless a task names them**, and then only as a proposed diff at a checkpoint.
6. **One-off recording helpers stay local.** D1's recorded mode is product code and is committed; a throwaway
   script for recording a single take is not.
7. **Never weaken `verify` or ground truth to make a number move.** They share rules (`api/app/graph/support.py`);
   a change to one is a change to both, and is called out at the checkpoint. From G1, held-out and hard cases
   are scored against hand labels, which no rule change touches.
8. **The PHI vault stays.** Graph state holds only placeholders and shifted dates, and the vault is never
   written to disk.

## Not in this plan

SSE live updates (UI step 9) and real payer or EHR integration, as in the roadmap. Picking SSE up later
replaces polling and changes no other milestone.
