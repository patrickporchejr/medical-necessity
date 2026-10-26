# Human QA checkpoints

Every milestone stops at a checkpoint. The orchestrator gives you a **checkpoint packet**: the PR, what changed,
how it was tested, known issues, and the decisions it needs from you. Then you work through the section below
and answer `pass`, `pass with follow-ups`, or `fail` with notes. Record the answer in [STATUS.md](STATUS.md),
or say it in chat and the orchestrator records it.

D15 trimmed the plan to M1, G1, M3, Q1 and D1. CP0, CP1 and CP3 are written out; CPG1, CPQ1 and CPD1 are
outlines, filled in when the orchestrator plans that milestone, so you review them at the checkpoint before.
Sections for cut milestones are kept below the line, as history.

Common setup, unless a section says otherwise:

```
git checkout m<N>/<slug>            # the milestone branch from the packet
cd api && uv sync && cd ../web && npm install && cd ..
docker compose up --build           # api :8000, mcp :8001, web :3000
```

Scripted models are the default. A step that needs a live model says so, with its estimated cost.

---

## CP0: the plan

**What you are checking:** that this plan is the right work, in the right order, with the right stops.

1. Read [README.md](README.md). Does the sequence match what you want built? Is anything missing or cuttable?
2. Read [LAB_STORE.md](LAB_STORE.md). Is it what you meant by "some patients' TB and HBV results come
   directly from another source"?
3. Skim [ORCHESTRATOR.md](ORCHESTRATOR.md) and one agent brief, such as [agents/API.md](agents/API.md).
   Are the rules and limits right?
4. Skim [M01](milestones/M01_cohort_and_eval_hygiene.md) and [M02](milestones/M02_reference_lab.md). Are they
   concrete enough to start?

**Decide** (the recommendation is first):

| # | Decision | Options |
|---|---|---|
| D1 | Reference lab as its own service, or a second store in the existing FHIR server | **Own service** (realistic, tests two sources and a lab outage) / same server (less work) |
| D2 | Screening mix across the cohort | **As proposed in LAB_STORE.md** / adjust the shares |
| D3 | Where a positive TB or HBsAg result lands in the worklist | **Resolve flagged claims, reason "screening positive"** / a sixth lane, *Clinical follow-up* |
| D4 | How eval selection covers screening situations | **Existing six strata, plus at least 5 cases per screening situation overall** / cross the axes (36 strata, a much larger cohort) |
| D5 | Where tasks are tracked | **STATUS.md only** / also GitHub issues / also Linear (the MED-* tickets were canceled at closeout) |
| D6 | The README says the project is complete. When does it change, and who drafts it? | **The orchestrator proposes a diff at CP3, you approve** / leave it until M7 / you write it |
| D7 | Scope | **All of M1–M12, with M11 the first to cut** / stop after M7 (a working worklist) / other |
| D8 | Agent definitions in `.claude/agents/` | **Create from the briefs after this checkpoint, models as suggested in ORCHESTRATOR.md** / adjust |

**Pass when:** the decisions are answered and the plan is changed to match. The orchestrator then makes those
edits, sets up the agents, and opens M1.

---

## CP1: bigger cohort, eval hygiene

**Setup:** no compose needed. The cohort is already generated in `data/synthea/fhir/` (it takes about a minute per
patient, so the data agent runs it before the checkpoint).

1. Run the strata report: `uv run --project api python evals/strata.py`. Every situation should have at least
   5 cases, or the packet should explain which can't and why.
2. Open the held-out file the packet names. Check that it lists case keys only, is committed, and that the
   packet states how it was picked (fixed seed, stratified).
3. Open the dry experiment summaries the packet names (one per split, from the scripted model, made by the qa
   agent). Each should carry per-node `seconds` (p50 and max per node) next to the scores.
4. Run the full test suite: `cd api && uv run pytest -q ../api ../evals`, then the cohort sweep the packet
   reports (`-m cohort`, about 20 minutes), or read its result in the packet.

**Pass when:** strata are filled or explained, the split is frozen, latency is in the summary, and tests pass.
**Decide:** the cohort size, if the data agent found the target too large to generate in reasonable time.

---

## CPG1: independent reference labels and hard cases (outline)

Run these from the main checkout, which has the cohort (`data/synthea` is gitignored, so a fresh checkout of
the branch has no charts); docker compose is not needed.

Open the chart viewer on one held-out case (`uv run --project api python evals/chart.py 53a6be67`) and read its
model labels on the review page (`cd web && npm run dev`, then `/labels`); the reasons should cite records that
exist and say what they show. Read the agreement report (`uv run --project api python evals/agreement.py`):
every disagreement between computed ground truth and the model labels, and between the model labels and the
hard split's answers, is a rule finding, a suspected labeler error or a recorded ambiguity, and no label was
edited. Read the hard split's cases and expected outcomes in `data/hard/cases.yaml` (hard charts open with
`evals/chart.py <key> --fhir-dir data/synthea/hard`, such as 977def3e). Confirm D21's five judgment calls as the
answers now carry them (977def3e's `active_disease` is gap: the injected "joint pain" line is not evidence).
Check that the caveats say no human reviewed the labels. Confirm no rule changed
(`git diff main..g1/ground-truth -- api/app/graph/` is empty). **Live spend:** the labeling run, approved before
it runs, not at the checkpoint.

---

## CP3: baseline run (Run 4)

**Needs live models.** The packet carries the estimate: at Run 3 rates, about $0.004 per run, times the case
count × runs × 2 models (3 runs on held-out and hard, 1 on working). You approve the spend before the run, not
at the checkpoint.

1. Read the new Run 4 section the evals agent drafted for `evals/RESULTS.md`. It reports accuracy, citation
   resolution, cost and latency per model, with **the working, held-out and hard splits in separate tables**,
   counts beside rates.
2. Check that it says this is a new baseline on independent labels, not a comparison with Run 3, and that the
   labels were written by a model and not reviewed by a human (D19).
3. Read the list of hard cases the rules got wrong. That list is Q1's input.
4. Check whether Haiku's first-draft misses on the "no order" cases recur (the roadmap's question).
5. Pick two cases from the packet and read their rows in the summary files (per-case run files are not
   committed); confirm the reported citation resolution matches. For example, held-out 681b83bb (Haiku is
   `experiments[0]`, Flash `[1]`):
   `jq -c '.experiments[0].cases[] | select(.name|endswith("681b83bb")) | [.name,.scores.first_pass_citation_resolution_rate,.scores.citation_resolution_rate,.scores.gap_accuracy,.scores.repaired]' evals/results/run-4/experiment_20261026T000151Z_held_out.json`,
   and hard 7c0d5d13 in `experiment_20261026T000648Z_hard.json`.
6. Review the proposed README diff (D6).

**Pass when:** you accept the numbers as the baseline every later milestone is compared with.

---

## CPQ1: faithful wording and fixes, Run 5 (outline)

**Needs live models**, approved before the run as in CP3. Run 5 is in RESULTS.md beside Run 4, per split,
first draft and final, with counts. Every rule change links to the Run 4 case that failed, and is scored on hand
labels. The quote check's catches on the hard split are listed, with its limit (a real quote can still be
misread) stated. Injection cases run in CI with scripted models. Check that the held-out split was not used to
tune, and that no improvement is claimed within run-to-run variation.

---

## CPD1: the demo, hosted (outline)

Open the hosted link as a stranger would: recorded mode, the banner saying recorded and synthetic, no model
calls. Walk the thin worklist (table, lanes and filter, a case in the pane, Next in queue, a decision recorded)
at desktop and 375px. Read the solution brief as a prior-auth lead would and check every number against its
source. Read `docs/HOW_THIS_WAS_BUILT.md` against the Workflow table in STATUS.md. You capture the Arcade
click-through yourself. Check the monthly hosting cost is a number in the repo (expected $0). Review the
proposed README diff.

---

**Cut by D15.** The sections below are kept as history and are not run.

---

## CP2: the reference lab

1. `curl` or open the lab service's patient listing from the packet. Pick one patient from each screening
   situation (the packet lists them).
2. In the dashboard, run a case for a **lab-only** patient. In the case page, the TB and HBsAg criteria show
   evidence, and the citation trace says **Reference Lab** with the accession id resolved.
3. Run an **EHR** patient: the same criteria cite **EHR** records.
4. Run a **positive** patient: TB (or HBsAg) is not established, and the reason says the screen was positive, not
   that it is missing.
5. Run a **pending** patient: both screens are gaps. Nothing cites the pending result.
6. Run a **nowhere** patient: both are gaps, as today.
7. Stop the lab container (`docker compose stop lab`) and run a case. It should fail with a message naming the
   lab, not produce a packet with false gaps. Start it again.
8. Read the diff to `support.py`, `criteria.py` and the YAML (the packet flags them). These change what `verify`
   and ground truth both believe.

**Pass when:** 2–7 behave as described, and you agree with the rule change in 8.

---

## CP4: run the worklist

1. Open the dashboard. The new shell renders (header, two panes, mint ground) at desktop width and at 375px,
   and the old pages still work inside it.
2. Press **Start worklist**. Every patient gets a case. The table shows each case's step and criterion marks
   changing without opening a case. Rows never reorder while runs progress.
3. Press **Pause**: running cases finish, and no new case starts. **Resume** continues.
4. The stat cards (drafted, running, waiting on you, cost) agree with the table.
5. Kill a case's run (the packet gives a way, such as a scripted failure) and use **Re-run**.
6. Read the concurrency measurement in the packet: cases per minute at 2, 8 and 25, and where the MCP chart
   cache starts to thrash. Decide the default `MAX_RUNNING`.

**Pass when:** one click runs the whole cohort and the screen follows it. **Decide:** the default concurrency.

---

## CP5: lanes and decisions

1. After a full worklist run, every finished case sits in exactly one lane, and the queue counts match the
   table filtered by lane.
2. **Resolve flagged claims:** mark one as a gap; override another (it refuses without a note).
3. **Ready to sign:** sign one. It needs the attestation, and it is only possible from the case view.
4. **Get missing records:** request records for a **pending-lab** patient. The case shows "waiting on records".
   The lab releases the result (the demo control from LAB_STORE.md), and the case resumes on its own: the
   screening criteria are now evidenced from Reference Lab, the other criteria were not re-run (the timeline
   shows it), and the case moves to Ready to sign. For a **nowhere** patient, request records: nothing
   arrives, and the screen says so.
5. **Confirm not eligible:** close one, return one to the prescriber.
6. **Runs that failed:** re-run.
7. A positive-screen case sits in Resolve flagged claims with the reason "screening positive" (D3).
8. Reload the page: decisions are still there. The audit log lists every decision with time and action.
9. Read the checkpoint-guard test in the packet: a planted real value is refused on a checkpoint write.
10. Run through the [WORKLIST_UI.md checklist](../WORKLIST_UI.md#checklist-for-every-step).

**Pass when:** every lane's actions work through the graph, and records arrive from the lab. Known limit: an API
restart loses waiting cases until M7. Your decisions here are kept as reviewer data for M10.

---

## CPX1: per-criterion fan-out (experiment)

**Needs live models.** You approve the estimate before the run.

1. Read the X1 section in `evals/RESULTS.md`: both modes side by side, held-out separate, with run-to-run
   variance stated.
2. Check the keep rule from the milestone file against the numbers, not against the recommendation.
3. Open one fan-out run's trace: one subagent per criterion, and a repair that re-sent only the flagged ones.

**Decide:** keep (`assemble_mode = "fanout"` by default), or drop (setting stays `single`; code removed or kept
off). Either answer passes the checkpoint.

---

## CP6: reviewer UX (outline)

Case detail in the left pane, Next in queue (within a lane filter), Esc, page restored on return; pagination
with 120 cases; every action at 375px without horizontal scroll; the WORKLIST_UI.md checklist.

---

## CP7: durable state and batch limits (outline)

A 50-case batch under a stated cost cap. Stop the API container mid-batch and start it again: running cases
continue from their last checkpoint, cases waiting on you are still waiting, and decisions survive. Query the
checkpoint table yourself and find no real names, ids or dates. Resolve a case and confirm it can no longer be
rehydrated (its key is shredded). Review the proposed README wording for the vault.

---

## CP8: prompt-injection hardening (outline)

The injection cases (in notes and in lab `comment`s) are in the eval set and in CI. Packet outcomes are unchanged
by them; any injected claim ends flagged. You decide whether keyword matches inside an injected line are a
finding or the accepted limit of keyword rules. Per-run and per-batch token caps refuse a run over the cap.

---

## CP9a / CP9b: second service line (outline)

9a: you approve or reject each proposed criteria YAML. Nothing enters `data/payer_criteria/` without you.
9b: the second line runs end to end with a reported score on the M3 eval setup, held-out separate.

---

## CP10: learning from reviewers (outline)

The improver's PR, with before and after scores on the working and held-out sets. You merge or reject it.
The packet says how many reviewer decisions it learned from, and that they are synthetic and one reviewer's.

---

## CP11: denials and appeals (outline)

A denied case round-trips: denial in, cited appeal draft out, your decision logged.

---

## CP13: the demo (outline)

Approve the storyboard (buyer story and technical deep-dive) before anything is built for it. Read the solution
brief as a hospital's prior-auth lead would, and check every number against its source. Then: reset brings back the starting state every time;
each scenario plays the same way twice in recorded mode with no model spend; the guided tour walks both
stories at desktop width; you capture the click-through in Arcade yourself and export the LinkedIn video; the hosted demo opens behind the password in recorded mode.

---

## CP12a / CP12b: deploy (outline)

12a, before anything is provisioned: the architecture, the monthly cost of leaving it up, the spend cap, and the
access gate. You approve or change them. 12b: open the demo as a stranger would, run a batch within the cap,
and check that the cost figure is in the repo.
