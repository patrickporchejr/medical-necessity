# Execution plan

Status: draft for checkpoint 0, 2026-10-24. Nothing below has started. This plan turns
[ROADMAP.md](../ROADMAP.md) and [WORKLIST_UI.md](../WORKLIST_UI.md) into an ordered sequence of
milestones, carried out by one orchestrator agent and a small set of subagents, with a human QA
checkpoint at the end of every milestone. It adds one piece neither doc has: a synthetic reference lab
that some patients' TB and hepatitis B results come from ([LAB_STORE.md](LAB_STORE.md)).

## How to read this

Start here, then go only as deep as you need:

| Level | Read | For |
|---|---|---|
| 1 | This page | The sequence, the checkpoints, who does what |
| 2 | [HUMAN_QA.md](HUMAN_QA.md) | What you test at each checkpoint and what you decide |
| 2 | [ORCHESTRATOR.md](ORCHESTRATOR.md) | How the work is dispatched, tracked and gated |
| 2 | [STATUS.md](STATUS.md) | Where the work is right now |
| 3 | [milestones/](milestones/) | One file per milestone: tasks, owners, order, done-when |
| 3 | [agents/](agents/) | One brief per subagent: scope, files it owns, rules |
| 3 | [LAB_STORE.md](LAB_STORE.md) | Design of the new reference lab |

The orchestrator reads levels 1 and 2 at the start of every session, and a milestone file when that
milestone starts. A subagent is handed its own brief and one task from one milestone file, not the whole plan.

## The sequence

Each milestone ends at a checkpoint (CP). Work does not move past a checkpoint until you mark it passed in
[STATUS.md](STATUS.md).

| # | Milestone | Source | Main agents | Checkpoint |
|---|---|---|---|---|
| M0 | [This plan](milestones/M00_plan.md) | — | orchestrator | **CP0** review the plan, answer the open decisions |
| M1 | [Bigger cohort, eval hygiene](milestones/M01_cohort_and_eval_hygiene.md) | Roadmap Phase 0 | data, evals | **CP1** strata counts, held-out split frozen |
| M2 | [Reference lab and screening labs](milestones/M02_reference_lab.md) | New | data, api, evals, web | **CP2** lab results reach a packet and its citation trace |
| M3 | [Baseline run (Run 4)](milestones/M03_baseline_run.md) | Roadmap Phase 0 | evals | **CP3** accept the new baseline numbers |
| M4 | [Run the worklist](milestones/M04_run_the_worklist.md) | UI steps 1–3, Phase 1 | api, web | **CP4** one click runs the cohort |
| M5 | [Lanes and decisions](milestones/M05_lanes_and_decisions.md) | UI steps 4–5, Phase 1 | api, web, data | **CP5** clear every lane, records arrive from the lab |
| M6 | [Reviewer UX](milestones/M06_reviewer_ux.md) | UI steps 6–8 | web | **CP6** desktop and 375px walkthrough |
| M7 | [Durable state and batch limits](milestones/M07_durable_state.md) | Phase 1 | api, infra | **CP7** 50-case batch survives a restart under a cost cap |
| M8 | [Prompt-injection hardening](milestones/M08_security.md) | Phase 5 | security, evals | **CP8** injection cases in CI, outcomes unchanged |
| M9 | [Second service line](milestones/M09_second_service_line.md) | Phase 2 | criteria, api, evals | **CP9a** approve proposed criteria; **CP9b** score on the new line |
| M10 | [Learning from reviewers](milestones/M10_learning_from_reviewers.md) | Phase 3 | improver, evals | **CP10** merge or reject the improver's PR |
| M11 | [Denials and appeals](milestones/M11_denials_and_appeals.md) | Phase 4 | api, evals, web | **CP11** a denied case round-trips |
| M12 | [Deploy on AWS](milestones/M12_deploy.md) | Phase 6 | infra | **CP12a** approve cost and design; **CP12b** a stranger runs a batch |

Why this order:

- **The lab store comes before the baseline run.** It changes the expected outcome of every RA-active case
  (TB and hepatitis B stop being gaps for some patients), so running the baseline first would mean paying
  for it twice and comparing against numbers that no longer apply.
- **The cohort grows before the lab store**, but the lab data is a pure function of the patient id, so growing
  the cohort later never reshuffles who has which result.
- **The worklist backend and UI go step by step together** (M4–M6), as WORKLIST_UI.md lays out, so every
  checkpoint has something to click.
- **Records arriving (M5) uses the lab store.** WORKLIST_UI.md step 5 says "records received" has to stay a
  manual decision because Synthea cannot add a record. A lab result released after the request makes it real.
- **Security (M8) sits before the second service line** so the injection cases are in the eval set before
  new criteria are scored against it. The roadmap calls it cheap and says to interleave it.
- **Learning from reviewers (M10) comes after M9**, because it needs reviewer decisions to learn from. The
  decisions you make during CP5–CP9 are that data (one reviewer, synthetic cases; HUMAN_QA.md says so).
- **M11 is the first to cut** (the roadmap's call). **M12 may start in parallel after CP7**, since the state
  store shapes the deployment; it still waits for its own cost approval at CP12a.

Detail matches distance: M1–M5 are planned to the task, M6–M8 to the step, and M9–M12 to the outcome.
The orchestrator writes the detailed plan for a later milestone at the checkpoint before it, when what was
learned is known. Each detailed plan is reviewed at that checkpoint.

## Who does what

| Agent | Brief | Owns |
|---|---|---|
| Orchestrator | [ORCHESTRATOR.md](ORCHESTRATOR.md) | The sequence, dispatch, STATUS.md, checkpoint packets. Writes no product code |
| data | [agents/DATA.md](agents/DATA.md) | Cohort generation, the reference lab's data and server |
| evals | [agents/EVALS.md](agents/EVALS.md) | Ground truth, experiments, RESULTS.md |
| api | [agents/API.md](agents/API.md) | The graph, PHI gateway, case API, dispatcher, persistence |
| web | [agents/WEB.md](agents/WEB.md) | The reviewer dashboard |
| qa | [agents/QA.md](agents/QA.md) | Verifies every task before it is accepted, and assembles the checkpoint packet |
| security | [agents/SECURITY.md](agents/SECURITY.md) | Injection cases and run caps (M8) |
| criteria | [agents/CRITERIA.md](agents/CRITERIA.md) | Criteria ingestion and the second service line (M9) |
| improver | [agents/IMPROVER.md](agents/IMPROVER.md) | Proposed prompt and criteria changes from reviewer data (M10). Never sees the held-out set |
| infra | [agents/INFRA.md](agents/INFRA.md) | Postgres, containers, AWS (M7, M12) |

Agents are added when a milestone needs them, in line with the roadmap's first rule: security, criteria,
improver and infra have no work before M7.

## Rules every agent follows

From the roadmap, and from how this repo has been run:

1. **Synthetic data only.** No PHI in the repo, a prompt, a trace or a deployment.
2. **No score without an eval behind it.** Held-out cases are reported apart from tuning cases.
3. **Humans approve consequential changes.** Agents open PRs; you merge. Nothing past a checkpoint without you.
4. **No live model spend or cloud resource without an estimate and a yes.** Scripted models by default.
5. **No README or existing doc edits unless a task names them**, and then only as a proposed diff at a checkpoint.
6. **Demo-only code stays local.** It is never committed or pushed.
7. **Never weaken `verify` or ground truth to make a number move.** They share rules (`api/app/graph/support.py`);
   a change to one is a change to both, and is called out at the checkpoint.

## Not in this plan

SSE live updates (UI step 9) and real payer or EHR integration, as in the roadmap. Picking SSE up later
replaces polling and changes no other milestone.
