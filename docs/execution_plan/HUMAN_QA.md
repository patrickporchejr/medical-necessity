# Human QA checkpoints

Every milestone stops at a checkpoint. The orchestrator gives you a **checkpoint packet**: the PR, what changed,
how it was tested, known issues, and the decisions it needs from you. Then you work through the section below
and answer `pass`, `pass with follow-ups`, or `fail` with notes. Record the answer in [STATUS.md](STATUS.md),
or say it in chat and the orchestrator records it.

Sections for CP0 to CP5 are written out. Later ones are outlines, and the orchestrator fills them in when it
plans that milestone, so you review them at the checkpoint before.

Common setup, unless a section says otherwise:

```
git checkout m<N>/<slug>            # the milestone branch from the packet
cd api && uv sync && cd ../web && npm install && cd ..
docker compose up --build           # api :8000, mcp :8001, lab :8002 from M2, web :3000
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

1. Run the strata report from the packet (`uv run python -m evals.experiment --report-strata`, or as given).
   Every situation should have at least 5 cases, or the packet should explain which can't and why.
2. Open the held-out file the packet names. Check that it lists case keys only, is committed, and that the
   packet states how it was picked (fixed seed, stratified).
3. Open a dry experiment summary from the scripted model. It should carry per-node `seconds` (p50 and max per
   node) next to the scores.
4. Run the full test suite: `cd api && uv run pytest -q ../api ../evals`.

**Pass when:** strata are filled or explained, the split is frozen, latency is in the summary, and tests pass.
**Decide:** the cohort size, if the data agent found the target too large to generate in reasonable time.

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

## CP3: baseline run (Run 4)

**Needs live models.** The packet carries the estimate: at Run 3 rates, about $0.004 per run, times the case
count × 3 runs × 2 models. You approve the spend before the run, not at the checkpoint.

1. Read the new Run 4 section the evals agent drafted for `evals/RESULTS.md`. It reports accuracy, citation
   resolution, cost and latency per model, with **held-out cases in a separate table**.
2. Check that it says this is a new baseline (the lab changed ground truth), not a comparison with Run 3.
3. Check whether Haiku's first-draft misses on the "no order" cases recur (the roadmap's question).
4. Pick two cases from the packet and open their run files; confirm the reported citation resolution matches.
5. Review the proposed README diff, if D6 put it here.

**Pass when:** you accept the numbers as the baseline every later milestone is compared with.

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
4. **Get missing records:** request records for a **pending-lab** patient. The lab releases the result (the
   demo control from LAB_STORE.md). Re-run: the screening criteria are now evidenced from Reference Lab, and
   the case moves to Ready to sign. For a **nowhere** patient, request records: nothing arrives, and the screen
   says so.
5. **Confirm not eligible:** close one, return one to the prescriber.
6. **Runs that failed:** re-run.
7. A positive-screen case sits where D3 decided, with its reason.
8. Reload the page: decisions are still there. The audit log lists every decision with time and action.
9. Run through the [WORKLIST_UI.md checklist](../WORKLIST_UI.md#checklist-for-every-step).

**Pass when:** every lane's actions work and records arrive from the lab. Your decisions here are kept as
reviewer data for M10.

---

## CP6: reviewer UX (outline)

Case detail in the left pane, Next in queue (within a lane filter), Esc, page restored on return; pagination
with 120 cases; every action at 375px without horizontal scroll; the WORKLIST_UI.md checklist.

## CP7: durable state and batch limits (outline)

A 50-case batch under a stated cost cap; stop the API container mid-batch and start it again, and unfinished
cases re-run while finished ones and all decisions survive; retries with backoff on a provider error; the note
in the packet on storing rehydrated packets with real ids, which you accept or reject.

## CP8: prompt-injection hardening (outline)

The injection cases (in notes and in lab `comment`s) are in the eval set and in CI. Packet outcomes are unchanged
by them; any injected claim ends flagged. You decide whether keyword matches inside an injected line are a
finding or the accepted limit of keyword rules. Per-run and per-batch token caps refuse a run over the cap.

## CP9a / CP9b: second service line (outline)

9a: you approve or reject each proposed criteria YAML. Nothing enters `data/payer_criteria/` without you.
9b: the second line runs end to end with a reported score on the M3 eval setup, held-out separate.

## CP10: learning from reviewers (outline)

The improver's PR, with before and after scores on the working and held-out sets. You merge or reject it.
The packet says how many reviewer decisions it learned from, and that they are synthetic and one reviewer's.

## CP11: denials and appeals (outline)

A denied case round-trips: denial in, cited appeal draft out, your decision logged.

## CP12a / CP12b: deploy (outline)

12a, before anything is provisioned: the architecture, the monthly cost of leaving it up, the spend cap, and the
access gate. You approve or change them. 12b: open the demo as a stranger would, run a batch within the cap,
and check that the cost figure is in the repo.
